# Public Release Checklist

Use this checklist before changing the GitHub repository from private to public.

## Product Story

- The first screenful of the [README](../README.md) clearly explains what OMADS does.
- The README uses current UI media only.
- The quick start is short and copy-pasteable.
- The setup details live in [Getting Started](getting-started.md), not in the README hero flow.
- The repository description and topics on GitHub match the actual product.

## Documentation

- [README](../README.md) matches the current product behavior.
- [Getting Started](getting-started.md) matches the actual install and launch flow.
- [Architecture](architecture.md) still reflects the current module boundaries.
- [Live Smoke Tests](live-smoke-tests.md) still match the current demo assets and measured results.
- [CHANGELOG](../CHANGELOG.md) includes the major user-visible changes that matter to new visitors.

## Trust Signals

- `LICENSE` is present and correct.
- [CONTRIBUTING](../CONTRIBUTING.md) explains how contributors should work in the repo.
- [SECURITY](../SECURITY.md) explains how private vulnerability reports should be sent.
- [SUPPORT](../SUPPORT.md) gives users a clear path for general, commercial, or direct contact.
- [CODE_OF_CONDUCT](../CODE_OF_CONDUCT.md) defines the community standard for public collaboration.
- GitHub issue templates are present and useful.
- The repository has a clear description and topics on GitHub.

## Validation

- `pytest` passes on the current branch.
- A clean local start works with `./start-omads.sh` or `.\start-omads.ps1`.
- The GUI opens and can register a local project.
- At least one short live smoke test still passes.

## GitHub Readiness

- Decide whether the repository should stay issue-only or also enable Discussions.
- Decide whether OMADS should remain MIT/open source or move to a source-available license with commercial restrictions before going public.
- Decide whether the first public release should be tagged as a GitHub Release immediately or after early feedback.
- Double-check that no secrets, local paths, or sensitive screenshots remain in tracked files.

## Nice-To-Have Before Public Launch

- Add one short GIF that shows a real builder -> breaker loop end to end.
- Add a social preview image for the GitHub repository page.
- Add a short comparison section in the README if users may ask why OMADS is better than using two terminals manually.
