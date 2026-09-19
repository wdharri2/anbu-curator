# Anbu Photo Curator

**Anbu Photo Curator** is a local-first AI-assisted photo curation system for turning large personal photo libraries into coherent, reviewable social-media post candidates.

Rather than simply ranking individual photos, Anbu tries to understand groups of images as events, scenes, stories, and potential posts. It combines AI-assisted discovery with human editorial feedback so repeated curation passes can produce progressively better results.

The current workflow intentionally keeps final publishing under human control.

## What It Does

Anbu Photo Curator currently supports:

* Photo ingestion and local library management
* AI-assisted image analysis
* Scene and event grouping
* Candidate post discovery
* Portfolio-level arbitration between competing post ideas
* Full post curation and sequencing
* Cover-photo consideration
* Human editorial review
* Per-photo review decisions
* Candidate rejection and recuration
* Candidate replacement and supersession
* Editorial-feedback persistence
* Review history
* Thumbnail generation and caching
* SQLite-backed state and provenance
* Local web review interface
* Manual publishing workflow

The pipeline is designed so metadata such as timestamps and event grouping can help the curator without becoming rigid boundaries.

---

# Architecture

At a high level:

```text
Photos
  ↓
Ingest
  ↓
AI Analysis
  ↓
Organization
  ↓
Scenes / Events
  ↓
Post Discovery
  ↓
Portfolio Arbitration
  ↓
Full Post Curation
  ↓
Human Review
  ↓
Publish / Recurate / Reject
```

Human feedback from reviewed candidates is retained and can influence later curation passes.

## Core Components

The project is organized around several major responsibilities:

```text
src/curator/
├── curate/
│   ├── discovery
│   └── arbitration
├── pipeline/
│   └── library
├── review/
│   └── posts_web
└── db
```

The exact structure may evolve as the project develops.

---

# Requirements

Anbu Photo Curator is currently intended to run on a Linux system with:

* Python 3
* SQLite
* Sufficient local or attached storage for the photo library
* An AI API key for configured analysis and curation models

A persistent server is convenient but not required.

---

# Installation

Clone the repository:

```bash
git clone https://github.com/wdharri2/anbu-curator.git
cd anbu-curator
```

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install the project dependencies using the dependency configuration included with the repository.

For an editable Python package installation, this will typically be:

```bash
pip install -e .
```

---

# Configuration

Anbu Photo Curator uses environment variables for local configuration.

Create a `.env` file in the project root.

Example:

```dotenv
OPENAI_API_KEY=your_api_key_here

CURATOR_DATA_ROOT=/path/to/data
CURATOR_DB=/path/to/data/db/curator.sqlite3
CURATOR_WORK_ROOT=/path/to/workspace
```

Do not commit `.env` or API credentials to source control.

Storage-heavy directories such as incoming photos, generated thumbnails, caches, and post assets can be placed on larger attached storage by configuring the work root appropriately.

---

# Running the Curator

Activate the environment:

```bash
cd anbu-curator
source .venv/bin/activate
```

Run the library pipeline:

```bash
python -m curator.pipeline.library
```

The pipeline processes the available library and performs the applicable stages of analysis, organization, discovery, arbitration, and curation.

Previously completed work is tracked in SQLite so the pipeline can avoid unnecessarily repeating expensive operations.

Human recuration requests can intentionally cause relevant material to be reconsidered.

---

# Running the Review Interface

Start the web interface:

```bash
scripts/run-web.sh
```

By default, the review application is available on the configured HTTP port.

For a local machine, this is typically:

```text
http://localhost:8080/posts
```

A systemd service or another process manager can also be used for persistent deployments.

---

# Review Workflow

The primary review queue is:

```text
/posts
```

This contains active candidate posts that still require a decision.

Historical views are available separately:

```text
/posts/published
/posts/rejected
/posts/recurated
```

Superseded candidates are retained internally for provenance but are not part of the normal review queue.

---

# Photo-Level Review Controls

Each photo in a candidate can receive an editorial disposition.

## Use Here

The photo belongs in this post concept.

This is the strongest positive signal that the curator correctly associated the image with the proposed post.

## Recurate

The photo is useful, but its current treatment is not right.

Examples include:

* It belongs in a different post
* It should be considered with a broader group
* The current sequencing is poor
* The concept is too narrow
* Another curation attempt should reconsider it

## Suppress

The photo should not be proposed again unless its state is manually restored.

This is useful for:

* Weak images
* Accidental captures
* Redundant images
* Photos that should never become social-media candidates

---

# Candidate-Level Actions

## Mark Published

Use this when the candidate reflects what was actually posted.

Publishing records the final post state and makes it part of the active portfolio context used during future arbitration.

## Recurate Candidate

Use this when the underlying post idea is worth keeping but the execution needs another attempt.

Examples:

* Wrong cover
* Poor sequencing
* Missing important photos
* Concept is good but too narrow
* Concept needs consolidation with related material

Non-suppressed photos in the candidate are currently made eligible for another serious curation attempt.

## Reject Candidate

Use this when the proposed post concept itself should not survive.

Photo-level dispositions are still preserved, allowing useful images to remain available without preserving the rejected grouping.

---

# Editorial Notes

Each candidate can include human editorial feedback.

Useful feedback is specific about *why* a candidate succeeded or failed.

For example:

```text
These images are from one continuous outing and should be consolidated
into a single stronger post rather than split into multiple room-specific
posts.
```

or:

```text
Keep this concept, but choose a stronger portrait from the same event as
the cover and improve the transition between the first and second half.
```

Editorial feedback becomes context for later discovery and arbitration passes.

---

# Recuration

Recuration is one of the core ideas behind Anbu.

Instead of treating AI curation as a one-shot generation task, Anbu keeps review history and lets the same library be reconsidered after human feedback.

A typical cycle is:

```text
Initial library
      ↓
Generate candidates
      ↓
Human review
      ↓
Publish / Reject / Recurate
      ↓
Store editorial feedback
      ↓
Run pipeline again
      ↓
Generate improved candidates
```

The goal is for the curator to improve its decisions from structured human feedback rather than requiring the user to start over manually.

---

# Portfolio Arbitration

Candidate generation is not performed in isolation.

Before a discovered idea becomes a full candidate, Anbu compares it against the existing portfolio.

The arbitration layer can currently:

```text
select
replace
alternative
reject
```

## Select

The proposal deserves its own post.

## Replace

The proposal is a better or broader treatment of one or more existing draft candidates.

The previous drafts are marked as superseded only after the replacement candidate is successfully created.

Published posts are not automatically replaced.

## Alternative

The proposal has merit but competes too closely with something already represented.

## Reject

The proposal should not become a post.

This prevents every visually distinct room, scene, or moment from automatically becoming its own candidate.

---

# Candidate States

Post candidates may currently have states including:

```text
candidate
published
recurate
rejected
superseded
```

### `candidate`

Active and awaiting human review.

### `published`

Accepted as a real published post.

### `recurate`

The idea or its photos should receive another curation attempt.

### `rejected`

The candidate concept was rejected but remains available as editorial history.

### `superseded`

A better candidate replaced the draft.

Superseded candidates remain in the database for provenance.

---

# Data Storage

Anbu Photo Curator currently uses SQLite for application state.

The database stores information such as:

* Photos
* Analysis state
* Curation state
* Scenes and events
* Candidate posts
* Candidate-photo membership
* Review dispositions
* Editorial feedback
* Published posts
* AI runs
* Processing state
* Storage metadata

Generated files such as thumbnails and intermediate artifacts are stored separately from the database.

---

# Intermediate AI Results

AI work is intentionally retained rather than treated as disposable output.

This makes it possible to:

* Inspect why a candidate was created
* Compare curation generations
* Debug unexpected decisions
* Preserve provenance
* Evaluate future prompt and model changes
* Avoid repeating some expensive work

---

# Current Limitations

Anbu Photo Curator is still under active development.

Some current limitations include:

* Manual position changes during review are not yet persisted as structured recuration feedback
* Candidate-level recuration currently promotes all non-suppressed photos in that candidate back into recuration
* Publishing to social networks remains manual
* Feed-aware cover selection is still limited
* Caption generation is not yet part of the complete end-to-end workflow
* The web interface is primarily an internal review tool rather than a polished public application
* Multi-user isolation and authentication are not yet implemented

---

# Upcoming Features

## Review and Feedback

* Persist reviewer-defined photo ordering during recuration
* Distinguish more precisely between "correct in this post" and "good photo, wrong grouping"
* Richer candidate-history browsing
* Better visualization of superseded candidate chains
* Structured cover-photo feedback
* Side-by-side comparison of recuration generations

## Curation

* Improved event-aware post consolidation
* Better detection of overly fragmented candidate sets
* Stronger cover-photo selection
* Better sequencing based on narrative flow
* Additional portfolio-level diversity controls
* Automated evaluation of whether later passes actually improve on rejected drafts

## Feed Awareness

* Existing-feed synchronization
* Grid-aware cover selection
* Feed-level visual diversity scoring
* Detection of repetitive recent subjects or compositions
* Post timing and immediacy context
* Feed-aware candidate arbitration

## Captions

* AI-assisted caption generation
* Caption generation informed by post story and editorial notes
* Style profiles
* Caption alternatives
* Final human review before publishing

## Import and Storage

* Automatic camera import
* Automatic mobile-photo import
* Background ingest
* Incremental library synchronization
* Storage quotas
* Progressive compression
* Archival tiers
* Remote archive support

## Multi-User Support

* Authentication
* Per-user photo libraries
* Per-user database isolation
* Per-user storage quotas
* Independent editorial histories
* Configurable model and curation preferences

## Publishing

The current philosophy is to keep posting manual until the curator is trustworthy enough to warrant deeper integration.

Future possibilities include:

* Export-ready post packages
* Final image ordering
* Caption export
* Publishing checklists
* Optional social-platform integrations

---

# Design Principles

## Human Editorial Control

AI proposes. The user decides.

Publishing, suppression, rejection, and editorial intent remain explicit human decisions.

## Metadata Is Context, Not Law

Capture time, location, event detection, and other metadata can inform curation without creating hard boundaries.

Photos from separate moments may belong together, while photos taken minutes apart may tell different stories.

## Preserve Provenance

Rejected and superseded ideas are useful information.

Anbu retains historical decisions so later passes can understand what has already been tried.

## Prefer Coherent Posts Over Maximum Output

The goal is not to generate as many posts as possible.

A smaller set of strong, distinct posts is preferable to many fragmented candidates from the same experience.

## Local-First Storage

Original photos, application state, and generated working files remain under the user's control.

---

# Development Status

Anbu Photo Curator is an active prototype.

The core ingest → analyze → discover → arbitrate → curate → review → recurate loop is operational, but interfaces, schemas, prompts, and workflows may continue to change.

Database migrations should be used as the schema evolves rather than assuming a fresh database for every version.

---

# Security

Never commit:

```text
.env
API keys
authentication tokens
private keys
personal photo libraries
database files containing private metadata
generated review data containing private information
```

Before deploying Anbu Photo Curator outside a trusted network, review the web application's authentication, network exposure, file-serving behavior, and secret-management configuration.

---

# License

Anbu Photo Curator is **source-available software**, not open-source software.

It is distributed under the **Anbu Source-Available License v1.0**.

The license permits:

- Self-hosting and use of the unmodified software
- Personal, educational, research, commercial, and internal organizational use
- Public or private hosting of the unmodified software
- Private or internal modification

Without prior written permission, the license does not permit:

- Publishing or distributing modified versions
- Publicly hosting a modified version
- Incorporating Anbu source code into another product or codebase
- Redistributing or sublicensing the source code
- Using Anbu source code, prompts, documentation, or repository contents for AI or machine-learning training

These points are a summary only. See the complete [LICENSE](LICENSE) for the governing terms.

Third-party components remain subject to their respective licenses. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
