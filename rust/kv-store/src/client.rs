//! A client for the cluster.
//!
//! Requests go to any reachable node. A follower redirects writes and
//! linearizable reads to the leader, and the HTTP client follows. While an
//! election is under way, or the node tried is down, the client moves on to
//! the next node and keeps trying until [`Client::retry_for`] runs out.
//!
//! Limitation: a write retried after a timeout may have gone through the
//! first time, so writes are at-least-once: a retried `put` can land after a
//! newer write to the same key, and a retried `delete` may report the key as
//! already gone.
//!
//! ```no_run
//! # async fn example() -> Result<(), kv_store::client::ClientError> {
//! use kv_store::client::Client;
//!
//! let client = Client::new(["kv1:7000", "kv2:7000", "kv3:7000"])?;
//! client.put("greeting", "hello").await?;
//! assert_eq!(client.get("greeting").await?.as_deref(), Some("hello"));
//! # Ok(())
//! # }
//! ```

use std::time::Duration;

use reqwest::{Method, StatusCode, Url};
use tokio::time::{self, Instant};

use crate::server::Status;

const RETRY_FOR: Duration = Duration::from_secs(10);
const REQUEST_TIMEOUT: Duration = Duration::from_secs(5);
const RETRY_BACKOFF: Duration = Duration::from_millis(200);

/// A client for a kv-store cluster.
#[derive(Debug, Clone)]
pub struct Client {
    http: reqwest::Client,
    nodes: Vec<Node>,
    retry_for: Duration,
}

/// A node's address, checked once so building URLs can't fail later.
#[derive(Debug, Clone)]
struct Node {
    addr: String,
    base: Url,
}

impl Node {
    fn parse(addr: &str) -> Result<Self, ClientError> {
        let invalid = || ClientError::InvalidNode(addr.to_owned());
        let base = Url::parse(&format!("http://{addr}/")).map_err(|_| invalid())?;
        // Anything past host:port would be silently dropped from requests.
        if base.path() != "/" || base.query().is_some() || base.host().is_none() {
            return Err(invalid());
        }
        Ok(Self {
            addr: addr.to_owned(),
            base,
        })
    }

    fn key_url(&self, key: &str) -> Url {
        let mut url = self.base.clone();
        url.path_segments_mut()
            .expect("http URLs have a path")
            .clear()
            .extend(["kv", key]);
        url
    }

    fn status_url(&self) -> Url {
        self.base
            .join("status")
            .expect("a fixed relative path joins")
    }
}

/// Why a request failed.
#[derive(Debug, thiserror::Error)]
pub enum ClientError {
    /// A node address isn't a valid `host:port`.
    #[error("invalid node address {0:?}: expected HOST:PORT")]
    InvalidNode(String),
    /// Every node was unreachable, or had no leader, until the retry
    /// deadline passed.
    #[error("cluster unavailable: {last_error}")]
    Unavailable { last_error: String },
    /// A node answered with a status the client didn't expect.
    #[error("unexpected {status}: {}", body.trim())]
    Unexpected { status: StatusCode, body: String },
    /// A request to one specific node couldn't be sent or its response read.
    #[error(transparent)]
    Http(#[from] reqwest::Error),
}

impl Client {
    /// A client for the nodes at `nodes`, each a `host:port`.
    ///
    /// # Errors
    ///
    /// [`ClientError::InvalidNode`] if an address isn't a `host:port`.
    pub fn new<I>(nodes: I) -> Result<Self, ClientError>
    where
        I: IntoIterator,
        I::Item: AsRef<str>,
    {
        let nodes = (nodes.into_iter())
            .map(|addr| Node::parse(addr.as_ref()))
            .collect::<Result<_, _>>()?;
        Ok(Self {
            http: http_client(REQUEST_TIMEOUT),
            nodes,
            retry_for: RETRY_FOR,
        })
    }

    /// Sets how long to keep retrying while no node can serve a request. The
    /// default is 10s.
    #[must_use]
    pub fn retry_for(mut self, retry_for: Duration) -> Self {
        self.retry_for = retry_for;
        self
    }

    /// Sets how long to wait for any one node to answer. The default is 5s.
    #[must_use]
    pub fn request_timeout(mut self, timeout: Duration) -> Self {
        self.http = http_client(timeout);
        self
    }

    /// The addresses of the nodes this client talks to.
    pub fn nodes(&self) -> impl Iterator<Item = &str> {
        self.nodes.iter().map(|node| node.addr.as_str())
    }

    /// Sets `key` to `value`.
    ///
    /// # Errors
    ///
    /// [`ClientError::Unavailable`] if no node could take the write in time,
    /// or [`ClientError::Unexpected`] if the leader refused it, for example
    /// because the value is too large.
    pub async fn put(&self, key: &str, value: &str) -> Result<(), ClientError> {
        let response = self.send(Method::PUT, key, Some(value)).await?;
        expect(response, StatusCode::NO_CONTENT).await.map(drop)
    }

    /// The value of `key`, read from the leader.
    ///
    /// # Errors
    ///
    /// [`ClientError::Unavailable`] if no node could serve the read in time,
    /// or [`ClientError::Unexpected`] for any answer but a value or `404`.
    pub async fn get(&self, key: &str) -> Result<Option<String>, ClientError> {
        let response = self.send(Method::GET, key, None).await?;
        optional_body(response).await
    }

    /// Removes `key`, returning whether it was there.
    ///
    /// # Errors
    ///
    /// [`ClientError::Unavailable`] if no node could take the delete in time,
    /// or [`ClientError::Unexpected`] if the leader refused it.
    pub async fn delete(&self, key: &str) -> Result<bool, ClientError> {
        let response = self.send(Method::DELETE, key, None).await?;
        if response.status() == StatusCode::NOT_FOUND {
            return Ok(false);
        }
        expect(response, StatusCode::NO_CONTENT).await.map(|_| true)
    }

    /// The value of `key` in `node`'s own copy, without asking the leader.
    /// May be out of date.
    ///
    /// # Errors
    ///
    /// [`ClientError::InvalidNode`] if `node` isn't a `host:port`,
    /// [`ClientError::Http`] if it can't be reached, or
    /// [`ClientError::Unexpected`] for any other answer.
    pub async fn get_local(&self, node: &str, key: &str) -> Result<Option<String>, ClientError> {
        let mut url = Node::parse(node)?.key_url(key);
        url.set_query(Some("stale=true"));
        let response = self.http.get(url).send().await?;
        optional_body(response).await
    }

    /// `node`'s view of the cluster.
    ///
    /// # Errors
    ///
    /// [`ClientError::InvalidNode`] if `node` isn't a `host:port`,
    /// [`ClientError::Http`] if it can't be reached, or
    /// [`ClientError::Unexpected`] for any answer but `200`.
    pub async fn status(&self, node: &str) -> Result<Status, ClientError> {
        let url = Node::parse(node)?.status_url();
        let response = self.http.get(url).send().await?;
        Ok(expect(response, StatusCode::OK).await?.json().await?)
    }

    /// Sends a key request to each node in turn until one can serve it.
    async fn send(
        &self,
        method: Method,
        key: &str,
        body: Option<&str>,
    ) -> Result<reqwest::Response, ClientError> {
        let deadline = Instant::now() + self.retry_for;
        let mut last_error = String::from("no nodes configured");
        for node in self.nodes.iter().cycle() {
            let mut request = self.http.request(method.clone(), node.key_url(key));
            if let Some(body) = body {
                request = request.body(body.to_owned());
            }
            match request.send().await {
                Ok(response) if response.status() == StatusCode::SERVICE_UNAVAILABLE => {
                    let body = response.text().await.unwrap_or_default();
                    last_error = format!("{}: {}", node.addr, body.trim());
                }
                Ok(response) => return Ok(response),
                Err(err) => last_error = format!("{}: {err}", node.addr),
            }
            if Instant::now() >= deadline {
                break;
            }
            time::sleep(RETRY_BACKOFF).await;
        }
        Err(ClientError::Unavailable { last_error })
    }
}

fn http_client(timeout: Duration) -> reqwest::Client {
    reqwest::Client::builder()
        .connect_timeout(Duration::from_secs(1).min(timeout))
        .timeout(timeout)
        .redirect(reqwest::redirect::Policy::limited(3))
        .build()
        .expect("HTTP client builds with default settings")
}

async fn expect(
    response: reqwest::Response,
    status: StatusCode,
) -> Result<reqwest::Response, ClientError> {
    if response.status() == status {
        Ok(response)
    } else {
        Err(ClientError::Unexpected {
            status: response.status(),
            body: response.text().await.unwrap_or_default(),
        })
    }
}

async fn optional_body(response: reqwest::Response) -> Result<Option<String>, ClientError> {
    if response.status() == StatusCode::NOT_FOUND {
        return Ok(None);
    }
    Ok(Some(expect(response, StatusCode::OK).await?.text().await?))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_host_and_port() {
        for addr in ["kv1:7000", "127.0.0.1:7000", "[::1]:7000", "localhost"] {
            assert!(Node::parse(addr).is_ok(), "{addr}");
        }
    }

    #[test]
    fn rejects_anything_else() {
        for addr in [
            "",
            "a b:7000",
            "kv1:7000/path",
            "kv1:7000?x=1",
            "kv1:notaport",
        ] {
            assert!(
                matches!(Node::parse(addr), Err(ClientError::InvalidNode(_))),
                "{addr}"
            );
        }
    }

    #[test]
    fn keys_are_percent_encoded_into_one_segment() {
        let node = Node::parse("kv1:7000").unwrap();
        assert_eq!(
            node.key_url("a/b c").as_str(),
            "http://kv1:7000/kv/a%2Fb%20c"
        );
        assert_eq!(node.status_url().as_str(), "http://kv1:7000/status");
    }
}
