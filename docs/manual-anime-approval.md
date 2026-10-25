# Manual anime publication approvals

The dispatcher is fail-closed: a series is not queued unless it is explicitly listed in
`data/anime_publication_approvals.json` under `approved_series`. New discoveries remain
pending and are never automatically published. A title listed in `denied_series` is blocked
even if it also appears in `approved_series`.

Use **Actions → Manage anime publication approval → Run workflow** to approve, deny, or
return a series to pending. Enter the series title without the episode number or quality
suffix, for example `Approved Saga`. The workflow commits the policy change to main, and
the normal dispatcher then re-evaluates the queue.

Changing a title to denied or pending prevents future queueing; it does not delete Telegram
posts that were already published. Manual approval is a publication preference only and
does not replace the separate redistribution-rights evidence gate.
