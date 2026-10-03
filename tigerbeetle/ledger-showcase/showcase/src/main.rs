//! A wallet/payments ledger on TigerBeetle through the official Rust client.
//!
//! Every money movement is a double-entry transfer, every invariant is enforced by the database:
//!
//! 1. accounts: issuers, fee revenue, FX liquidity, merchants, and USD + EUR wallets per user;
//!    wallets have `debits_must_not_exceed_credits`, so they can never go negative
//! 2. funding: the USD issuer credits every wallet
//! 3. payment storm: CLIENTS concurrent clients send PAYMENTS payments in full batches; each
//!    payment is a linked pair (payment + 1% fee), so both legs commit or neither does; random
//!    amounts overspend many wallets, and the database rejects every overdraft; then a payday
//! 4. holds: a card authorization is a pending transfer that reserves funds; then many holds,
//!    captured for less than the hold, voided, or left to time out
//! 5. currency exchange: a linked USD leg + EUR leg through liquidity accounts; when the EUR
//!    liquidity runs out, the USD leg rolls back with it
//! 6. idempotency: re-sending an already submitted batch changes nothing
//! 7. audit: every account is checked against a model built only from the replies, per ledger
//!    sum(debits) == sum(credits), and no limited account has debits above credits
//!
//! IDs are TigerBeetle time-based ids (`tb::id()`) plus an offset, so every run uses fresh
//! accounts and transfers on the same cluster.

use std::collections::BTreeMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

use futures::executor::block_on;
use tigerbeetle as tb;

const USD: u32 = 840; // ledger = ISO 4217 numeric code; amounts are cents
const EUR: u32 = 978;
const BATCH_MAX: usize = 8189; // events per request in the standard build

// transfer codes (what kind of money movement)
const C_FUND: u16 = 1;
const C_PAYMENT: u16 = 2;
const C_FEE: u16 = 3;
const C_P2P: u16 = 4;
const C_CARD: u16 = 5;
const C_FX: u16 = 6;

// fixed account slots; merchants and wallets follow
const ISSUER_USD: usize = 0;
const FEES: usize = 1;
const LP_USD: usize = 2;
const ISSUER_EUR: usize = 3;
const LP_EUR: usize = 4;
const ALICE: usize = 5;
const FIRST_MERCHANT: usize = 8;

const FUNDING: u128 = 50_000; // $500.00 per wallet
const LP_EUR_FUNDING: u128 = 5_000_000; // EUR 50,000.00 of exchange liquidity

fn env<T: std::str::FromStr>(name: &str, default: T) -> T {
    std::env::var(name).ok().and_then(|v| v.parse().ok()).unwrap_or(default)
}

/// SplitMix64: deterministic per seed, no dependency.
struct Rng(u64);
impl Rng {
    fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }
    fn below(&mut self, n: usize) -> usize {
        (self.next() % n as u64) as usize
    }
    fn range(&mut self, lo: u128, hi: u128) -> u128 {
        lo + (self.next() as u128) % (hi - lo + 1)
    }
}

/// Expected balances, built only from what the database acknowledged.
#[derive(Clone, Copy, Default, PartialEq, Eq, Debug)]
struct Bal {
    debits_posted: u128,
    credits_posted: u128,
    debits_pending: u128,
    credits_pending: u128,
}

struct Layout {
    account_base: u128,
    merchants: usize,
    users: usize,
}
impl Layout {
    fn first_wallet(&self) -> usize {
        FIRST_MERCHANT + self.merchants
    }
    fn usd_wallet(&self, u: usize) -> usize {
        self.first_wallet() + u
    }
    fn eur_wallet(&self, u: usize) -> usize {
        self.first_wallet() + self.users + u
    }
    fn count(&self) -> usize {
        self.first_wallet() + 2 * self.users
    }
    fn id(&self, idx: usize) -> u128 {
        self.account_base + idx as u128
    }
    fn idx(&self, id: u128) -> usize {
        (id - self.account_base) as usize
    }
}

struct Ids(u128, AtomicU64);
impl Ids {
    fn next(&self) -> u128 {
        self.0 + self.1.fetch_add(1, Ordering::Relaxed) as u128
    }
}

fn transfer(
    id: u128,
    debit: u128,
    credit: u128,
    amount: u128,
    ledger: u32,
    code: u16,
    flags: tb::TransferFlags,
) -> tb::Transfer {
    tb::Transfer {
        id,
        debit_account_id: debit,
        credit_account_id: credit,
        amount,
        ledger,
        code,
        flags,
        ..Default::default()
    }
}

fn submit(client: &tb::Client, events: &[tb::Transfer]) -> Vec<tb::CreateTransferResult> {
    let results = block_on(client.create_transfers(events).expect("client closed"))
        .expect("create_transfers request failed");
    assert_eq!(results.len(), events.len());
    results
}

fn lookup(client: &tb::Client, ids: &[u128]) -> Vec<tb::Account> {
    let mut out = Vec::with_capacity(ids.len());
    for chunk in ids.chunks(BATCH_MAX) {
        out.extend(
            block_on(client.lookup_accounts(chunk).expect("client closed"))
                .expect("lookup_accounts request failed"),
        );
    }
    out
}

fn post(model: &mut [Bal], l: &Layout, t: &tb::Transfer) {
    model[l.idx(t.debit_account_id)].debits_posted += t.amount;
    model[l.idx(t.credit_account_id)].credits_posted += t.amount;
}

fn created(r: &tb::CreateTransferResult) -> bool {
    r.status == tb::CreateTransferStatus::Created
}

/// The status that decided a linked chain: the first event that is not `linked event failed`.
fn chain_status(results: &[tb::CreateTransferResult]) -> tb::CreateTransferStatus {
    results
        .iter()
        .map(|r| r.status)
        .find(|s| *s != tb::CreateTransferStatus::LinkedEventFailed)
        .unwrap_or(tb::CreateTransferStatus::LinkedEventFailed)
}

fn tally(counts: &BTreeMap<String, u64>) -> String {
    counts.iter().map(|(k, v)| format!("{k}: {v}")).collect::<Vec<_>>().join(", ")
}

fn usd(cents: u128) -> String {
    format!("${}.{:02}", cents / 100, cents % 100)
}

fn pct(sorted: &[Duration], p: f64) -> f64 {
    let i = ((sorted.len() as f64 - 1.0) * p).round() as usize;
    sorted[i].as_secs_f64() * 1e3
}

fn main() {
    let address: String = env("TB_ADDRESS", "127.0.0.1:3000".to_string());
    let users: usize = env("USERS", 10_000);
    let merchants: usize = env("MERCHANTS", 100);
    let payments: usize = env("PAYMENTS", 500_000);
    let clients: usize = env("CLIENTS", 2);
    let holds: usize = env("HOLDS", 30_000);
    let exchanges: usize = env("EXCHANGES", 20_000);

    let l = Layout { account_base: tb::id(), merchants, users };
    let ids = Ids(tb::id() + (1 << 40), AtomicU64::new(0));
    let client = tb::Client::new(0, &address).expect("client init");
    let mut model = vec![Bal::default(); l.count()];
    let mut rng = Rng(0x5EED);

    println!("TigerBeetle ledger showcase (Rust client), cluster 0 at {address}");
    println!(
        "{users} users (USD + EUR wallet each), {merchants} merchants, {payments} payments from \
         {clients} clients, {holds} card holds, {exchanges} currency exchanges\n"
    );

    // 1. accounts ------------------------------------------------------------------------------
    let limited = tb::AccountFlags::DebitsMustNotExceedCredits;
    let accounts: Vec<tb::Account> = (0..l.count())
        .map(|idx| {
            let (ledger, code, flags) = match idx {
                ISSUER_USD => (USD, 1, tb::AccountFlags::default()),
                ISSUER_EUR => (EUR, 1, tb::AccountFlags::default()),
                FEES | LP_USD => (USD, 2, tb::AccountFlags::default()),
                LP_EUR => (EUR, 2, limited),
                ALICE => (USD, 10, limited),
                i if i >= FIRST_MERCHANT && i < l.first_wallet() => (USD, 3, limited),
                i if i < l.first_wallet() => (USD, 9, tb::AccountFlags::default()), // spare
                i if i < l.first_wallet() + users => (USD, 10, limited),
                _ => (EUR, 10, limited),
            };
            tb::Account { id: l.id(idx), ledger, code, flags, ..Default::default() }
        })
        .collect();
    let t = Instant::now();
    for chunk in accounts.chunks(BATCH_MAX) {
        let results = block_on(client.create_accounts(chunk).unwrap()).unwrap();
        assert!(results.iter().all(|r| r.status == tb::CreateAccountStatus::Created));
    }
    println!(
        "1. accounts        {} created in {:.0} ms; wallets, merchants and EUR liquidity have \
         debits_must_not_exceed_credits",
        accounts.len(),
        t.elapsed().as_secs_f64() * 1e3
    );

    // 2. funding -------------------------------------------------------------------------------
    fund_wallets(&client, &l, &ids, &mut model);
    let lp = transfer(ids.next(), l.id(ISSUER_EUR), l.id(LP_EUR), LP_EUR_FUNDING, EUR, C_FUND, Default::default());
    assert!(created(&submit(&client, &[lp])[0]));
    post(&mut model, &l, &lp);
    println!(
        "2. funding         {} each to {users} wallets; {} EUR liquidity",
        usd(FUNDING),
        usd(LP_EUR_FUNDING).replace('$', "")
    );

    // 3. payment storm -------------------------------------------------------------------------
    // Each payment is a linked pair: payer -> payee (merchant or another user) and payer -> fees.
    // A full request holds BATCH_MAX events; keep chains whole: (BATCH_MAX / 2) pairs.
    let pairs_per_batch = BATCH_MAX / 2;
    let per_client = payments.div_ceil(clients);
    // A new client registers a session with the cluster on its first request; do that (one
    // lookup) in every client before the clock starts.
    let ready = std::sync::Barrier::new(clients + 1);
    let mut started = Instant::now();
    let outcomes: Vec<_> = std::thread::scope(|s| {
        let handles: Vec<_> = (0..clients)
            .map(|c| {
                let (l, ids, address, ready) = (&l, &ids, &address, &ready);
                s.spawn(move || {
                    let client = tb::Client::new(0, address).expect("client init");
                    lookup(&client, &[l.id(ISSUER_USD)]);
                    ready.wait();
                    let mut rng = Rng(c as u64 + 1);
                    let mut deltas = vec![Bal::default(); l.count()];
                    let mut counts = BTreeMap::<String, u64>::new();
                    let mut latencies = Vec::new();
                    let mut last_batch = Vec::new();
                    let mine = per_client.min(payments.saturating_sub(c * per_client));
                    let mut left = mine;
                    while left > 0 {
                        let n = left.min(pairs_per_batch);
                        left -= n;
                        let mut batch = Vec::with_capacity(2 * n);
                        for _ in 0..n {
                            let payer = rng.below(users);
                            let (payee, code) = if rng.below(10) < 7 {
                                (FIRST_MERCHANT + rng.below(merchants), C_PAYMENT)
                            } else {
                                let mut other = rng.below(users);
                                if other == payer {
                                    other = (other + 1) % users;
                                }
                                (l.usd_wallet(other), C_P2P)
                            };
                            let amount = rng.range(100, 5_000);
                            let fee = (amount / 100).max(1);
                            let from = l.id(l.usd_wallet(payer));
                            let linked = tb::TransferFlags::Linked;
                            batch.push(transfer(ids.next(), from, l.id(payee), amount, USD, code, linked));
                            batch.push(transfer(ids.next(), from, l.id(FEES), fee, USD, C_FEE, Default::default()));
                        }
                        let t = Instant::now();
                        let results = submit(&client, &batch);
                        latencies.push(t.elapsed());
                        for (pair, res) in batch.chunks(2).zip(results.chunks(2)) {
                            if res.iter().all(created) {
                                pair.iter().for_each(|t| post(&mut deltas, l, t));
                            }
                            *counts.entry(chain_status(res).to_string()).or_default() += 1;
                        }
                        last_batch = batch;
                    }
                    (deltas, counts, latencies, last_batch)
                })
            })
            .collect();
        ready.wait();
        started = Instant::now();
        handles.into_iter().map(|h| h.join().unwrap()).collect()
    });
    let elapsed = started.elapsed();

    let mut counts = BTreeMap::<String, u64>::new();
    let mut latencies = Vec::new();
    let mut replay = Vec::new();
    for (deltas, c, lat, first) in outcomes {
        for (m, d) in model.iter_mut().zip(deltas) {
            m.debits_posted += d.debits_posted;
            m.credits_posted += d.credits_posted;
        }
        c.into_iter().for_each(|(k, v)| *counts.entry(k).or_default() += v);
        latencies.extend(lat);
        if replay.is_empty() {
            replay = first; // client 0's last batch: late in the storm, so it has rejections
        }
    }
    latencies.sort();
    let transfers = (2 * payments) as f64;
    println!(
        "3. payment storm   {payments} payments = {} transfers (payment + fee, linked) in {:.2} s \
         = {:.0} transfers/s, {} requests of up to {} events, batch p50 {:.1} ms / p99 {:.1} ms",
        2 * payments,
        elapsed.as_secs_f64(),
        transfers / elapsed.as_secs_f64(),
        latencies.len(),
        2 * pairs_per_batch,
        pct(&latencies, 0.5),
        pct(&latencies, 0.99)
    );
    println!("                   payments: {}", tally(&counts));
    println!(
        "                   fees collected {} on one hot account; every wallet overspend rejected",
        usd(model[FEES].credits_posted)
    );

    // payday: the storm drained most wallets; credit them again before the next phases
    fund_wallets(&client, &l, &ids, &mut model);
    println!("                   payday: {} to every wallet again", usd(FUNDING));

    // 4. holds (two-phase transfers) -----------------------------------------------------------
    // Alice: a hold reserves funds, so a payment that fits her posted balance still fails.
    let merchant = l.id(FIRST_MERCHANT);
    let alice = l.id(ALICE);
    let fund = transfer(ids.next(), l.id(ISSUER_USD), alice, 10_000, USD, C_FUND, Default::default());
    let mut hold = transfer(ids.next(), alice, merchant, 8_000, USD, C_CARD, tb::TransferFlags::Pending);
    hold.timeout = 3600;
    let pay = |id| transfer(id, alice, merchant, 3_000, USD, C_PAYMENT, Default::default());
    let first_try = pay(ids.next());
    let mut void = transfer(ids.next(), alice, merchant, 8_000, USD, C_CARD, tb::TransferFlags::VoidPendingTransfer);
    void.pending_id = hold.id;
    let second_try = pay(ids.next());
    let mut alice_says = Vec::new();
    for t in [&fund, &hold, &first_try, &void, &second_try] {
        alice_says.push(submit(&client, std::slice::from_ref(t))[0].status);
    }
    for (t, s) in [&fund, &second_try].iter().zip([alice_says[0], alice_says[4]]) {
        assert_eq!(s, tb::CreateTransferStatus::Created);
        post(&mut model, &l, t);
    }
    assert_eq!(alice_says[1], tb::CreateTransferStatus::Created);
    assert_eq!(alice_says[2], tb::CreateTransferStatus::ExceedsCredits);
    assert_eq!(alice_says[3], tb::CreateTransferStatus::Created);
    println!(
        "4. holds           alice has $100.00, hold $80.00 -> {}; pay $30.00 -> {}; void hold -> \
         {}; pay $30.00 -> {}",
        alice_says[1], alice_says[2], alice_says[3], alice_says[4]
    );

    // Many holds: a third captured for 80% of the hold, a third voided, a third expire after 1 s.
    let mut pending = Vec::with_capacity(holds);
    for k in 0..holds {
        let user = l.usd_wallet(rng.below(users));
        let mut t = transfer(
            ids.next(),
            l.id(user),
            l.id(FIRST_MERCHANT + rng.below(merchants)),
            rng.range(500, 20_000),
            USD,
            C_CARD,
            tb::TransferFlags::Pending,
        );
        t.timeout = if k % 3 == 2 { 1 } else { 3600 };
        pending.push(t);
    }
    let mut hold_counts = BTreeMap::<String, u64>::new();
    let mut open = Vec::new();
    for chunk in pending.chunks(BATCH_MAX) {
        for (t, r) in chunk.iter().zip(submit(&client, chunk)) {
            *hold_counts.entry(r.status.to_string()).or_default() += 1;
            if created(&r) {
                model[l.idx(t.debit_account_id)].debits_pending += t.amount;
                model[l.idx(t.credit_account_id)].credits_pending += t.amount;
                open.push(*t);
            }
        }
    }
    let expiring: Vec<tb::Transfer> = open.iter().filter(|t| t.timeout == 1).copied().collect();
    let closing: Vec<tb::Transfer> = open
        .iter()
        .filter(|t| t.timeout != 1)
        .enumerate()
        .map(|(k, p)| {
            let (flags, amount) = if k % 2 == 0 {
                (tb::TransferFlags::PostPendingTransfer, p.amount * 8 / 10)
            } else {
                (tb::TransferFlags::VoidPendingTransfer, p.amount)
            };
            let mut t = transfer(ids.next(), p.debit_account_id, p.credit_account_id, amount, USD, C_CARD, flags);
            t.pending_id = p.id;
            t
        })
        .collect();
    let by_id: std::collections::HashMap<u128, tb::Transfer> = open.iter().map(|t| (t.id, *t)).collect();
    let (mut captured, mut voided) = (0u128, 0u64);
    for chunk in closing.chunks(BATCH_MAX) {
        for (t, r) in chunk.iter().zip(submit(&client, chunk)) {
            assert!(created(&r), "closing a hold: {}", r.status);
            let p = by_id[&t.pending_id];
            model[l.idx(p.debit_account_id)].debits_pending -= p.amount;
            model[l.idx(p.credit_account_id)].credits_pending -= p.amount;
            if t.flags == tb::TransferFlags::PostPendingTransfer {
                post(&mut model, &l, t);
                captured += t.amount;
            } else {
                voided += 1;
            }
        }
    }
    // the replica expires pending transfers on its own clock; wait for the holds to drop out
    // (until then the model still counts them, so held = model - what the replica reports)
    let mut expiring_by_account = std::collections::HashMap::<u128, u128>::new();
    for t in &expiring {
        *expiring_by_account.entry(t.debit_account_id).or_default() += t.amount;
    }
    let expiring_accounts: Vec<u128> = expiring_by_account.keys().copied().collect();
    let wait = Instant::now();
    loop {
        std::thread::sleep(Duration::from_millis(250));
        let pending_left: u128 = lookup(&client, &expiring_accounts)
            .iter()
            .map(|a| a.debits_pending + expiring_by_account[&a.id] - model[l.idx(a.id)].debits_pending)
            .sum();
        if pending_left == 0 || wait.elapsed() > Duration::from_secs(30) {
            break;
        }
    }
    for p in &expiring {
        model[l.idx(p.debit_account_id)].debits_pending -= p.amount;
        model[l.idx(p.credit_account_id)].credits_pending -= p.amount;
    }
    let late = expiring.first().map(|p| {
        let mut t = transfer(ids.next(), p.debit_account_id, p.credit_account_id, p.amount, USD, C_CARD, tb::TransferFlags::PostPendingTransfer);
        t.pending_id = p.id;
        submit(&client, &[t])[0].status
    });
    println!(
        "                   {holds} holds: {}; {} captured at 80% ({}), {voided} voided, {} \
         expired after {:.1} s; capturing an expired hold -> {}",
        tally(&hold_counts),
        closing.len() as u64 - voided,
        usd(captured),
        expiring.len(),
        wait.elapsed().as_secs_f64(),
        late.map(|s| s.to_string()).unwrap_or_default()
    );

    // 5. currency exchange (linked legs through liquidity accounts) ----------------------------
    let mut fx = Vec::with_capacity(2 * exchanges);
    for _ in 0..exchanges {
        let u = rng.below(users);
        let dollars = rng.range(1_000, 10_000);
        let euros = dollars * 92 / 100;
        fx.push(transfer(ids.next(), l.id(l.usd_wallet(u)), l.id(LP_USD), dollars, USD, C_FX, tb::TransferFlags::Linked));
        fx.push(transfer(ids.next(), l.id(LP_EUR), l.id(l.eur_wallet(u)), euros, EUR, C_FX, Default::default()));
    }
    let mut fx_counts = BTreeMap::<String, u64>::new();
    let mut rolled_back = 0u64;
    for chunk in fx.chunks(2 * pairs_per_batch) {
        for (pair, res) in chunk.chunks(2).zip(submit(&client, chunk).chunks(2)) {
            if res.iter().all(created) {
                pair.iter().for_each(|t| post(&mut model, &l, t));
            } else if res[0].status == tb::CreateTransferStatus::LinkedEventFailed {
                rolled_back += 1; // the USD leg was fine, the EUR leg failed: both undone
            }
            *fx_counts.entry(chain_status(res).to_string()).or_default() += 1;
        }
    }
    println!(
        "5. exchange        {exchanges} USD->EUR at 0.92: {}; {rolled_back} USD legs rolled back \
         because EUR liquidity ran out (left: EUR {})",
        tally(&fx_counts),
        usd(model[LP_EUR].credits_posted - model[LP_EUR].debits_posted).replace('$', "")
    );

    // 6. idempotency ---------------------------------------------------------------------------
    let mut replay_counts = BTreeMap::<String, u64>::new();
    for r in submit(&client, &replay) {
        *replay_counts.entry(r.status.to_string()).or_default() += 1;
    }
    println!(
        "6. idempotency     re-sent a storm batch ({} transfers): {}",
        replay.len(),
        tally(&replay_counts)
    );

    // 7. audit ---------------------------------------------------------------------------------
    let all: Vec<u128> = (0..l.count()).map(|i| l.id(i)).collect();
    let found = lookup(&client, &all);
    assert_eq!(found.len(), all.len());
    let mut mismatched = 0;
    let mut negative = 0;
    let mut sums = BTreeMap::<u32, [u128; 4]>::new();
    for a in &found {
        let got = Bal {
            debits_posted: a.debits_posted,
            credits_posted: a.credits_posted,
            debits_pending: a.debits_pending,
            credits_pending: a.credits_pending,
        };
        if got != model[l.idx(a.id)] {
            mismatched += 1;
        }
        if a.flags == limited && a.debits_posted + a.debits_pending > a.credits_posted {
            negative += 1;
        }
        let s = sums.entry(a.ledger).or_default();
        s[0] += a.debits_posted;
        s[1] += a.credits_posted;
        s[2] += a.debits_pending;
        s[3] += a.credits_pending;
    }
    let balanced = sums.values().all(|s| s[0] == s[1] && s[2] == s[3]);
    println!("7. audit           {} accounts looked up", found.len());
    for (ledger, s) in &sums {
        println!(
            "                   ledger {ledger}: debits {} == credits {} ({}), pending {} / {}",
            usd(s[0]).replace('$', ""),
            usd(s[1]).replace('$', ""),
            if s[0] == s[1] { "ok" } else { "MISMATCH" },
            s[2],
            s[3]
        );
    }
    println!(
        "                   {mismatched} accounts differ from the client-side model, {negative} \
         limited accounts below zero, money in circulation {}",
        usd(found[ISSUER_USD].debits_posted)
    );
    let ok = balanced && mismatched == 0 && negative == 0;
    println!("\n{}", if ok { "AUDIT PASSED" } else { "AUDIT FAILED" });
    if !ok {
        std::process::exit(1);
    }
}

/// The USD issuer credits FUNDING to every USD wallet.
fn fund_wallets(client: &tb::Client, l: &Layout, ids: &Ids, model: &mut [Bal]) {
    let funding: Vec<tb::Transfer> = (0..l.users)
        .map(|u| {
            let to = l.id(l.usd_wallet(u));
            transfer(ids.next(), l.id(ISSUER_USD), to, FUNDING, USD, C_FUND, Default::default())
        })
        .collect();
    for chunk in funding.chunks(BATCH_MAX) {
        for (t, r) in chunk.iter().zip(submit(client, chunk)) {
            assert!(created(&r));
            post(model, l, t);
        }
    }
}
