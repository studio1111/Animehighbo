# Manual anime publication controls

All archive entries are eligible by default, including unfamiliar or ambiguous release titles. You do **not** need to approve titles one by one. The queue uses a cleaned release title as a fallback identity when it cannot confidently parse the series name, so those entries can still be queued for review in the Telegram channel.

To stop a series or a particular unfamiliar release title from being queued, use **Actions → Manage anime publication approval → Run workflow** and choose **deny** with the exact series title or cleaned release title. Choose **allow** to remove that title from the denylist. The denylist takes precedence over any legacy approved list. The workflow commits the decision to `main`, and the dispatcher re-evaluates the queue.

This is a queue preference, not proof of distribution rights. Actual episode uploads still require valid redistribution authorization evidence. A manual allow decision cannot replace or override that separate rights gate. Changing a title to denied prevents future queueing; it does not delete Telegram posts already published.
