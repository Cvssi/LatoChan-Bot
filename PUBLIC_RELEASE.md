# GitHub publication checklist

These sources are prepared locally; publication and production approval are separate steps.

1. The project uses the MIT license in `LICENSE`, preserved from the repository initialization. Review third-party dependency notices and attribution before distribution.
2. Confirm the repository URLs use the intended owner and names before publication.
3. Review exactly the files to publish. Never upload a working folder wholesale. Exclude real environment/configuration files, SSH keys, logs, notes, databases, personal personas, private review evidence, backups, staging bundles and virtual environments. `.gitignore` does not remove already tracked files or Git history.
4. Run a secret scan of the candidate files and full history, and inspect the staged diff. If credentials were ever committed, rotate them and clean the history before publishing. Do not paste secret-scan findings containing credentials into a public issue.
5. Run the documented tests and dependency checks on the supported platforms. Complete disposable-host provisioning, backup restoration, SSH permission checks and isolated Discord smoke tests before declaring a production-ready release.
6. Publish reviewed sources and sanitized release notes, then verify the GitHub workflow results. Tag a release only after these checks pass.

The software uses configurable Discord IDs, model names, SSH host/user and local paths. Public instructions must use placeholders rather than details from a particular installation.
