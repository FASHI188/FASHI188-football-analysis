# Football3 Formal GPT Runner Request Carrier

This branch exists only to keep one permanent Draft PR open as an audited request transport for the formal GPT runner.

- The executable workflow and gateway code are always checked out from the PR base SHA.
- Match requests are supplied only through the Draft PR body markers.
- Editing the Draft PR body does not require a per-match repository commit.
- This carrier must remain Draft and must not be merged into the integration branch.
- It is not a production CURRENT pointer and does not change model parameters, weights, formal enablement, or production state.
