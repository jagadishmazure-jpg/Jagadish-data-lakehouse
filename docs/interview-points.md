# Interview points

Short, honest talking points for Jagadish-data-lakehouse. Every number quoted is from the
real run shown in the README.

- A version exists only when its log entry exists; that is where atomicity comes from.
- Optimistic concurrency: check the version at commit, refuse if it moved.
- Vacuum trades storage for the time travel window; the demo shows the failure after it.
- I state the gaps plainly: full snapshots per version, in-place optimize, local filesystem only.

## Likely follow-up questions

- How does a real format commit on object storage? (Put-if-absent or a coordination service for the log entry.)
- Why does vacuum need a retention period, not 0? (Readers may still be using older files.)
- When would you use merge on read vs copy on write? (Write-heavy tables vs read-heavy tables.)
