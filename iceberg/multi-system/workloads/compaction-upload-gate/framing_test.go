package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"net/http"
	"strings"
	"testing"
)

// A fixed empty-payload SigV4 fixture, not a general signer or an S3 client.
// Recalculate at the backend from the actual received headers, including the
// signed Content-Length, so a transport framing change invalidates the HMAC.
func emptyFixtureAuthorization(r *http.Request, signedLength bool) string {
	const scope = "20261122/us-east-1/s3/aws4_request"
	names, headers := "host;x-amz-content-sha256;x-amz-date", ""
	if signedLength {
		names = "content-length;" + names
		headers = "content-length:" + r.Header.Get("Content-Length") + "\n"
	}
	headers += "host:" + r.Host + "\nx-amz-content-sha256:" + r.Header.Get("X-Amz-Content-Sha256") + "\nx-amz-date:" + r.Header.Get("X-Amz-Date") + "\n"
	canonical := r.Method + "\n" + r.URL.EscapedPath() + "\n" + r.URL.Query().Encode() + "\n" + headers + "\n" + names + "\n" + r.Header.Get("X-Amz-Content-Sha256")
	digest := sha256.Sum256([]byte(canonical))
	toSign := "AWS4-HMAC-SHA256\n20261122T120000Z\n" + scope + "\n" + hex.EncodeToString(digest[:])
	mac := func(key []byte, value string) []byte {
		h := hmac.New(sha256.New, key)
		h.Write([]byte(value))
		return h.Sum(nil)
	}
	key := []byte("AWS4local-fixture-secret")
	for _, part := range []string{"20261122", "us-east-1", "s3", "aws4_request"} {
		key = mac(key, part)
	}
	return "AWS4-HMAC-SHA256 Credential=fixture/" + scope + ", SignedHeaders=" + names + ", Signature=" + hex.EncodeToString(mac(key, toSign))
}

func TestSignedEmptyRequestsPreserveNoBodyAndFraming(t *testing.T) {
	for _, method := range []string{http.MethodPost, http.MethodPut, http.MethodHead} {
		t.Run(method, func(t *testing.T) {
			signedLength := method != http.MethodHead
			_, front, _ := fixtureMode(t, func(w http.ResponseWriter, r *http.Request) {
				body, err := io.ReadAll(r.Body)
				payloadSHA := sha256.Sum256(body)
				framingOK := err == nil && len(body) == 0 && r.ContentLength == 0 && len(r.TransferEncoding) == 0 && r.Header.Get("X-Amz-Content-Sha256") == hex.EncodeToString(payloadSHA[:])
				if signedLength {
					framingOK = framingOK && r.Header.Get("Content-Length") == "0"
				}
				signatureOK := hmac.Equal([]byte(r.Header.Get("Authorization")), []byte(emptyFixtureAuthorization(r, signedLength)))
				if !framingOK || !signatureOK {
					w.WriteHeader(http.StatusForbidden)
					io.WriteString(w, "SignatureDoesNotMatch")
					return
				}
				w.Header().Set("ETag", "verified-empty-signature")
				if method == http.MethodPost {
					io.WriteString(w, "<InitiateMultipartUploadResult><UploadId>fixture-upload</UploadId></InitiateMultipartUploadResult>")
				}
			}, false)
			path := "/warehouse/" + strings.TrimPrefix(testPrefix, "s3://warehouse/") + "empty.parquet"
			if method == http.MethodPost {
				path += "?uploads"
			}
			request, err := http.NewRequest(method, front.URL+path, http.NoBody)
			if err != nil {
				t.Fatal(err)
			}
			request.Host = "signed-gate:8333"
			request.Header.Set("X-Amz-Date", "20261122T120000Z")
			empty := sha256.Sum256(nil)
			request.Header.Set("X-Amz-Content-Sha256", hex.EncodeToString(empty[:]))
			if signedLength {
				request.Header.Set("Content-Length", "0")
			}
			request.Header.Set("Authorization", emptyFixtureAuthorization(request, signedLength))
			response, err := front.Client().Do(request)
			if err != nil {
				t.Fatal(err)
			}
			defer response.Body.Close()
			if response.StatusCode != http.StatusOK || response.Header.Get("ETag") != "verified-empty-signature" {
				t.Fatalf("empty signed request changed on the wire: status=%d", response.StatusCode)
			}
			body, err := io.ReadAll(response.Body)
			if err != nil || method == http.MethodPost && string(body) != "<InitiateMultipartUploadResult><UploadId>fixture-upload</UploadId></InitiateMultipartUploadResult>" || method != http.MethodPost && len(body) != 0 {
				t.Fatalf("signed request response changed: %q %v", body, err)
			}
		})
	}
}
