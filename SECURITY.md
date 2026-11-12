# Security

## Supported versions

| Version | Supported |
| ------- | --------- |
| 0.1.x   | yes       |

## Reporting a vulnerability

Please use this repository’s private security advisory flow (**Security → Advisories → Report a vulnerability** on GitHub). Do not open public issues for exploitable problems.

Include steps to reproduce, impact, and any suggested fix if you have one.

## Automated checks

GitHub Actions runs the security jobs on every push to `main`, on every pull request, and every Monday at 06:17 UTC.

`pip-audit` exports the lockfile and fails when a locked dependency has a known vulnerability. The export includes every extra and every dependency group, and it omits the local project.

```bash
uv export --frozen --all-extras --all-groups --no-emit-project --format requirements-txt
```

CodeQL analyzes the Python code with the `code-scanning` query suite. The job fails when a query reports a warning or an error.

Dependabot opens weekly pull requests for GitHub Actions versions and for uv updates.

Advisory ignores live in `.github/security/pip-audit-ignores.toml`. Each `[[ignore]]` entry has an `id`, a `reason`, and an `expires` date in `YYYY-MM-DD` form. The audit job accepts that entry through the `expires` date in UTC. The next day the job fails until you delete the entry or move the date.

The security workflow has no Trivy job. A Trivy 0.76 config scan of `docker/asterisk/Dockerfile` reports three findings. The image runs as root, it has no `HEALTHCHECK`, and `apt-get` omits `--no-install-recommends`. The entrypoint starts Asterisk as root. That Trivy version does not scan the compose files.
