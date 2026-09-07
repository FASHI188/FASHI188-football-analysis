"""Install the governed GitHub Actions API transport for formal gateway Python processes.

The patch is intentionally narrow: it only changes urllib handling for GitHub Actions
artifact/run endpoints. Non-GitHub and non-Actions requests pass through unchanged.
"""
from github_api_budget_transport_v1 import install

install()
