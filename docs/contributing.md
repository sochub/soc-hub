# Contributing

## PR title convention

PR titles must follow [Conventional Commits](https://www.conventionalcommits.org/):

    <type>(optional scope): <description>

Allowed types: `feat`, `fix`, `docs`, `ci`, `chore`, `refactor`, `test`, `perf`, `build`, `security`, `revert`.
Example: `fix(auth): throttle failed logins`.

## Labels (applied automatically)

| Source | Labels |
| --- | --- |
| Changed paths (`.github/labeler.yml`) | `backend`, `frontend`, `migrations`, `workflows-engine`, `slack`, `ai`, `auth`, `docs`, `ci`, `dependencies`, `docker`, `tests` |
| PR size (changed lines, `package-lock.json` ignored) | `size/XS` (<=10), `size/S` (<=100), `size/M` (<=500), `size/L` (<=1000), `size/XL` |
| PR title type | `feat` -> `enhancement`, `fix` -> `bug`, `docs` -> `documentation`; `ci`, `chore`, `refactor`, `test`, `perf`, `build`, `security`, `revert` map to the same-named label |

Labelling runs via `pull_request_target` (so it works for forks) and never checks out or runs PR code.
Path labels are only ever added, not removed, when a PR changes.

## PR checks (`.github/workflows/pr-checks.yml`)

| Job | What it does |
| --- | --- |
| Semantic PR title | Fails if the title is not a conventional-commit title with an allowed type. |
| Dependency review | Fails if the PR introduces dependencies with high or critical vulnerabilities; posts a summary comment (not on fork PRs, which lack write access). |
| Secret scan | TruffleHog scans the PR's diff range and fails on verified secrets only. |
| actionlint | Lints workflow files under `.github/workflows`. |
| hadolint | Lints `backend/Dockerfile` and `frontend/Dockerfile`. Non-blocking for now. |

## Dependency updates and action pinning

Dependabot opens weekly update PRs for GitHub Actions, npm (`frontend`), pip (`backend`) and Docker
(`backend`, `frontend`), grouping minor and patch updates per ecosystem (max 5 open PRs each).
Third-party actions are pinned to a full commit SHA with a `# vX.Y.Z` comment; Dependabot keeps them current.
Do not use tag or branch references in workflows.

## Pull request template

`.github/pull_request_template.md` asks for a summary, test plan, security impact and UI screenshots.
