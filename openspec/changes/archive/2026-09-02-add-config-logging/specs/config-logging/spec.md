## ADDED Requirements

### Requirement: Logging configuration

The system SHALL load logging configuration from the `logging` section of `config.yaml`, with the following keys: `level` (one of `debug`, `info`, `warning`, `error`), `file` (boolean), and `console` (boolean).

#### Scenario: Logging section present

- **GIVEN** `config.yaml` contains `logging.level=info`, `logging.file=true`, and `logging.console=false`
- **WHEN** the server or a script initializes logging
- **THEN** log records at `info` level and above are emitted, file logging is enabled, and console logging is disabled

#### Scenario: Logging section absent

- **GIVEN** `config.yaml` has no `logging` section
- **WHEN** the server or a script initializes logging
- **THEN** it uses defaults of `level=info`, `file=true`, and `console=false`

#### Scenario: Invalid level value

- **GIVEN** `config.yaml` contains `logging.level=verbose`
- **WHEN** the configuration is resolved
- **THEN** the system falls back to the default `info` level

### Requirement: Logging environment variable override

The system SHALL allow environment variables to override logging configuration values.

#### Scenario: Env var overrides config

- **GIVEN** `config.yaml` sets `logging.level=warning` and env var `GH_SEARCH_LOG_LEVEL=debug` is set
- **WHEN** the configuration is resolved
- **THEN** the effective log level is `debug`

### Requirement: File and console output control

The system SHALL independently control file and console logging via the `file` and `console` settings, and SHALL honor the `--debug` CLI flag as a console override.

#### Scenario: Debug flag forces console

- **GIVEN** a script is run with `--debug`
- **WHEN** logging initializes
- **THEN** console logging is enabled and the level is `debug`, regardless of `logging.console` and `logging.level`

#### Scenario: File logging disabled

- **GIVEN** `config.yaml` contains `logging.file=false`
- **WHEN** a script initializes logging
- **THEN** no rotating file handler is attached

### Requirement: REST service logging

The system SHALL initialize logging for the REST service so that request lifecycle events follow the configured logging settings.

#### Scenario: Service honors config

- **GIVEN** the REST service starts with `logging.level=info` and `logging.file=true`
- **WHEN** a request is processed
- **THEN** informational log records are written to the configured log file

### Requirement: Pipeline step script logging

The step scripts `enrich_metrics.py` and `fetch_readme.py` SHALL route progress information through the shared logging setup instead of unconditional `print` to stderr.

#### Scenario: Step script logs progress

- **GIVEN** `logging.file=true` and `logging.level=info`
- **WHEN** `enrich_metrics.py` or `fetch_readme.py` runs a pipeline step
- **THEN** progress information is written to the configured log file and is not emitted as unconditional stderr output when console logging is disabled
