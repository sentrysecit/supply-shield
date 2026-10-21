$ErrorActionPreference = "Continue"

Write-Host "Running lint and format checks (ruff)..."
uv run ruff check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

uv run ruff format --check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Running type checking (mypy)..."
uv run mypy
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Running security analysis (bandit)..."
uv run bandit -c pyproject.toml -r src
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Running dependency audit (pip-audit)..."
uv export --locked --no-emit-project --format requirements-txt --output-file requirements-audit.txt --quiet
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

uv run pip-audit --requirement requirements-audit.txt --require-hashes --disable-pip
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Running tests with coverage (pytest)..."
uv run pytest --cov --cov-report=term-missing --cov-report=xml
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "All checks passed successfully."
