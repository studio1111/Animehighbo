# Manual anime publication controls

All identifiable anime titles are eligible by default. You do **not** need to approve each title individually. To stop a series from being queued, add its exact series title to `denied_series`; the denylist takes precedence even if a title remains in the legacy `approved_series` list.

Use **Actions → Manage anime publication approval → Run workflow**. Choose **deny** to block a series or **allow** to remove it from the denylist. The workflow commits the change to `main`, and the dispatcher re-evaluates the queue. Titles that cannot be identified reliably are held rather than guessed.

This is a queue preference, not proof of distribution rights. Actual episode uploads still require valid redistribution authorization evidence. A manual allow decision cannot replace or override that separate rights gate. Changing a title to denied prevents future queueing; it does not delete Telegram posts already published.
