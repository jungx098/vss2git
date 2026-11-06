# Vss2Git

Vss2Git replays a Visual SourceSafe 6.0 database as Git history. The result must look like the project had always been in Git: renames, moves, deletes, shares, branches, pins, and labels included. Read `README.md` for user-facing behavior. This file is how to change the code without corrupting history.

## Layout

| Project | Role | TFM |
|---|---|---|
| `VssPhysicalLib` | Raw VSS record format (`Hpdi.VssPhysicalLib`) | `net10.0` |
| `VssLogicalLib` | Projects, files, revisions, actions (`Hpdi.VssLogicalLib`) | `net10.0` |
| `HashLib` | Checksums used by the physical reader | `net10.0` |
| `Vss2Git.Core` | Migration engine (`Hpdi.Vss2Git`) | `net10.0` |
| `Vss2Git.Cli` | `migrate` / `list` / `verify` (`Hpdi.Vss2Git.Cli`) | `net10.0` |
| `Vss2Git` | WinForms GUI, thin shell over Core | `net10.0-windows` |
| `VssDump` | Diagnostic dump of a VSS database | `net10.0` |
| `Vss2Git.Tests` | Unit tests. References the GUI project | `net10.0-windows` |
| `Vss2Git.Cli.Tests` | CLI option mapping and console interaction | `net10.0` |
| `Vss2Git.IntegrationTests` | End-to-end migrations of built scenarios | `net10.0` |
| `Vss2Git.TestDbBuilder` | Builds those scenarios with `ss.exe` / `mkss.exe` | `net10.0` |

`Vss2Git` and `Vss2Git.Tests` do not build on non-Windows hosts. Core, the CLI, the VSS libraries, CLI tests, and integration tests do.

## Pipeline

`MigrationOrchestrator` runs three stages on a single-worker `WorkQueue`:

1. `RevisionAnalyzer` walks the chosen VSS project, including later-deleted and renamed items. Shared files are read once.
2. `ChangesetBuilder` sorts revisions and groups them into commits with the time-and-comment windows (`AnyCommentSeconds` / `SameCommentSeconds`). One pending changeset per user. A file that would collide with itself flushes the changeset.
3. `GitExporter` replays changesets through `IGitRepository`. `VssPathMapper` is the live map of where every item is.

Backends, selected by `MigrationConfiguration.GitBackend`:

- `Process` — `GitWrapper`, one `git.exe` invocation per operation. The reference output.
- `LibGit2Sharp` — `LibGit2SharpRepository`.
- `FastImport` — `FastImportRepository`, one `git fast-import` stream. Also the backend that can continue an existing repo (`--from-date` / `--to-date`).

GUI and CLI must stay thin. New migration behavior goes in Core and is reached through `MigrationConfiguration`, `IUserInteraction`, and `IStatusReporter`. Keep GUI settings (`SettingsMapper`) and CLI flags (`CliOptionsMapper`) in sync when a setting is user-facing. CLI verbs are CommandLineParser types in `Vss2Git.Cli`; exit codes are `0` success, `1` bad args or startup failure, `2` migration finished with errors.

## Invariants

- The three backends produce the same commits, tags, and file bytes. A Git behavior change is implemented on `IGitRepository` in all three classes, then checked by `CrossBackendValidationTests` (LibGit2Sharp and FastImport compared with Process).
- Fast-import is strict: one `LF`, one space where the spec says `SP`, no CRLF. A bad stream writes `.git/fast_import_crash_*`. Read `GIT_FAST_IMPORT_FORMAT.md` before editing `FastImportRepository`.
- Path compares are ordinal ignore-case. Case-only renames are a two-step move.
- Same-second revisions stay causal: create, then edit, then delete.
- Shared and branched files become independent Git copies. A pin is tracked per file, not per project. Empty directories are not committed. VSS labels become annotated tags, globally scoped.
- One VSS project path per run. Disjoint subtrees are separate migrations and their commits do not interleave.
- The tool reads a VSS database. It does not write one. Migrations and experiments run against a copy, never a live share.
- `VssPhysicalLib` decodes an undocumented binary format. Change a record layout only when a real database or a TestDbBuilder scenario disagrees with the reader. `Visual SourceSafe Commands.md` is the command reference for building those scenarios.

## Build and test

```bash
dotnet build Vss2Git.sln -c Debug
dotnet test Vss2Git.Cli.Tests/Vss2Git.Cli.Tests.csproj -c Debug
dotnet test Vss2Git.Tests/Vss2Git.Tests.csproj -c Debug          # Windows only
dotnet test Vss2Git.IntegrationTests/Vss2Git.IntegrationTests.csproj -c Debug
```

CI (`.github/workflows/release.yml`, `windows-latest`, .NET 10) builds the solution and runs only `Vss2Git.Tests` and `Vss2Git.Cli.Tests`. Treat those two as the required check. Run the integration project when the change touches replay, path mapping, a backend, or changeset grouping.

Integration tests load `Vss2Git.IntegrationTests/TestData/<scenario>/vss_db`. That tree is gitignored. `Vss2Git.TestDbBuilder` creates it and needs Visual SourceSafe at `C:\Program Files (x86)\Microsoft Visual SourceSafe` (`ss.exe`, `mkss.exe`). A missing database throws `DirectoryNotFoundException`; that is an environment gap, not a product failure. Scenarios `01`–`13` live in `Vss2Git.TestDbBuilder/Scenarios`. The scenario list inside `CrossBackendValidationTests.Scenarios` is the list that must stay in sync. `Vss2Git.TestDbBuilder/README.md` stops at scenario 05.

## Where a change goes

- Changeset grouping, comment matching, timestamp order: `ChangesetBuilder` plus `Vss2Git.Tests/ChangesetBuilderTests.cs`.
- Where a file sits after rename, move, share, delete, recover: `VssPathMapper` plus `VssPathMapperTests` / `TranslatePendingPathsTests`.
- Exclude globs: `PathMatcher`.
- Replay of a VSS action into Git: `GitExporter`, then all three repositories if the Git operation itself changes.
- A new history bug: add a numbered scenario, an integration test next to the existing ones, and an entry in `CrossBackendValidationTests.Scenarios`.
- CLI flags: `CliOptions`, `CliOptionsMapper`, and `Vss2Git.Cli.Tests`. Document user-facing flags in `Vss2Git.Cli/README.md`.

## Style

Match the file you are editing. Do not modernize surrounding code.

- Production code uses block namespaces, explicit `using`s, and nullable disabled. `Vss2Git.Cli.csproj` sets `ImplicitUsings` and `Nullable` to `disable` on purpose.
- Types in Core that tests need are `internal`, exposed with `InternalsVisibleTo` for `Vss2Git.Tests` and `Vss2Git.IntegrationTests`.
- Tests are xUnit and FluentAssertions. `Vss2Git.Tests` still uses block namespaces; integration tests use file-scoped namespaces. Either is fine inside the test project you are editing.
- Keep the Apache 2.0 header on files that already have one. Do not stamp a new copyright year onto old HPDI headers.
- Do not hand-edit `*.Designer.cs`.
- Version fields in the csproj files move only when a release is being cut.

## Python port (`python/`)

- The Linux and macOS port lives under `python/`.
- The .NET tree is the behavior oracle. When the Python output and .NET output differ, .NET is right unless a real database proves otherwise.
- Tests copy `VSS2GIT_TEST_DB` into a temp directory rather than opening it for write. Never point a test or experiment at the original database.

## Docs

- `README.md` — behavior, backends, known limitations.
- `Vss2Git.Cli/README.md` — CLI options.
- `GIT_FAST_IMPORT_FORMAT.md` — stream format for `FastImportRepository`.
- `Visual SourceSafe Commands.md` — `ss.exe` commands used to build fixtures.
