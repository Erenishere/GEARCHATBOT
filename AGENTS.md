# Repository Guidelines

## Project Structure & Module Organization

This repository is currently a scaffold: it has no application source, tests, assets, or package manifest. When adding the first implementation, keep application code in `src/`, automated tests in `tests/`, and static files in `assets/`. Put configuration at the repository root and document any new top-level directory in the README. Keep generated files and installed dependencies out of version control.

## Build, Test, and Development Commands

There are no build, test, or local-run commands yet. Add a single documented entry point for each workflow when the toolchain is chosen (for example, scripts in `package.json` or targets in a `Makefile`). Before opening a pull request, run the project's formatter, linter, and test command once they exist. Update this section with the exact commands as part of the change that introduces them.

## Coding Style & Naming Conventions

No language-specific style or formatter is configured. Follow the conventions of the language and framework selected for the first implementation, and commit its formatter and linter configuration. Use descriptive names, keep related code together, and match existing file naming within each directory. Avoid mixing tabs and spaces in a file.

## Testing Guidelines

No test framework or coverage threshold is established. Add tests alongside each new feature or bug fix, using names that describe behavior, such as `tests/test_chat_response.py` or `tests/chat-response.test.ts`. Include a regression test for every fixed defect. Document how to run the suite when the framework is added.

## Commit & Pull Request Guidelines

No Git history is available here, so there is no established commit-message pattern. Use short, imperative subjects such as `Add chat response handler`. Pull requests should explain the change, list verification performed, and link any related issue. Include screenshots for visible interface changes and call out new configuration or setup steps.
