# Isolated Scaffolding

This directory is the default work area for Claude Code. Its contents are disposable development support; they must not be imported, called, built, or deployed by the noob-agent main application.

Allowed work includes mock connector responses, test fixtures and helpers, example manifests, contract-validation examples, diagrams, setup notes, and local prototype scripts. Prototype scripts must not access credentials, live game services, W&B, shared runtime configuration, or production code.

Each scaffold must document how to run it and how to remove it. Moving a scaffold into the main application requires an explicitly approved `hackathon_plan.md` section, a failing test written first, and a separate pull request.
