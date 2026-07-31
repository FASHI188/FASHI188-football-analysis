# Security and governance boundaries

The daily workflow is read-only. It has `contents: read`, pinned third-party actions, an exact-head checkout, no repository writeback, no provider credential, no provider endpoint, and no schedule. It records zero external requests and fails if the branch changes protected model/data/config/CURRENT paths.

PR review and merge remain separate user-controlled decisions.
