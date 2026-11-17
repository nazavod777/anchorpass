# Registry access for the first release

[← Release operations](release.md)

Registry credentials and uploads are a separate stage. Preparing and installing
the sealed `0.1.1` candidate requires no registry publishing token. Package names
are not reserved by a successful metadata lookup or local build.

The current publication workflow uses tokens. Trusted Publishing, which lets a
registry authorize a specific GitHub workflow without a long-lived token, would
be a separate workflow change and registry-account setup.

## Current status

`anchorpass` `0.1.1` is public on [PyPI](https://pypi.org/project/anchorpass/0.1.1/) and [npm](https://www.npmjs.com/package/anchorpass/v/0.1.1/). Public artifact bytes matched the approved SHA256 values, and fresh standalone installations passed outside the checkout. Replacement tokens were saved in both GitHub environment secrets with the requested `anchorpass` restrictions; the owner confirmed that the initial broad tokens were revoked.

Rust `0.1.1` is public on [crates.io](https://crates.io/crates/anchorpass/0.1.1); the downloaded archive matched the sealed SHA256 and publication was confirmed in the journal. A fresh Cargo consumer downloaded the registry dependency without a local path replacement, compiled successfully and passed offline embedded-data checks for async/blocking `load_bg` and `extract_action`. The permanent `CRATES_IO_TOKEN` is stored in the `anchorpass-production` environment with the requested `publish-update` scope restricted to `anchorpass`. The owner confirmed revocation of the temporary first-upload token. Go `v0.1.1` is public through the module proxy under `go/v0.1.1`. A fresh consumer installed it without a local replacement and passed race-enabled standalone API, embedded-data and authenticated CONNECT proxy checks with verified target TLS. Module integrity verification passed. See the [Go release record](go-release-0.1.1.json) for the exact commit and checksums. Go publication requires no registry API token.

Independent read-only inspection confirmed the PyPI token's project-name and
project-ID caveats, and npm accepted its replacement token for an authenticated
metadata request. npm and crates.io permission selections were not independently
read back; those restrictions are recorded from the requested account settings.

## Required access

| Service | First upload of `anchorpass` | Later uploads | GitHub environment secret |
| --- | --- | --- | --- |
| PyPI | Account API token; select `Entire account (all projects)` while the project does not exist | Replace it with a project-scoped `anchorpass` token and revoke the first token | `PYPI_API_TOKEN` |
| npm | Granular token; `Read and write (publish and stage)`, `All Packages` for the first new unscoped package, `Bypass two-factor authentication` for unattended direct publication; Organizations: `No access` | Replace it with a token restricted to `anchorpass` and revoke the first token | `NPM_TOKEN` |
| crates.io | Temporary `publish-new` token restricted to `anchorpass` | Replace with a `publish-update` token restricted to `anchorpass` and revoke the first token | `CRATES_IO_TOKEN` |
| Go | No registry API token; publishing uses the approved `go/v0.1.1` Git tag in the public repository | New explicit version tags | None |

Use a short expiry for first-upload tokens. These permissions permit package
publication; account passwords, organization administration and adding package
owners are not needed. Initial PyPI/npm tokens can affect other packages the
account owns until replaced with narrower tokens.

PyPI requires verified email and two-factor authentication. See its
[publishing and API-token instructions](https://docs.pypi.org/publishing/publishing-to-pypi/)
and [account requirements](https://docs.pypi.org/your-account/managing-your-account/).
Create a token in [PyPI account settings](https://pypi.org/manage/account/#api-tokens).

For npm, use the website's Access Tokens page; see
[creating granular tokens](https://docs.npmjs.com/creating-and-viewing-access-tokens/).
The current workflow calls `npm publish`, so a `Read and write (stage only)` token
does not work with it. npm documents direct token publication as deprecated with
removal planned for January 2027. Before that transition, migrate publication to
Trusted Publishing or implement the explicit staging and approval process; see
[npm access-token policy](https://docs.npmjs.com/about-access-tokens/).

For Rust, verify the account email and create the token at
[crates.io token settings](https://crates.io/settings/tokens). Endpoint scopes,
crate-name restrictions and expiry are described in the Rust team's
[API-token announcement](https://blog.rust-lang.org/2023/06/23/improved-api-tokens-for-crates-io/)
and the [Cargo publishing guide](https://doc.rust-lang.org/cargo/reference/publishing.html).
No owner-management or yank permission is needed for the release workflow.

## Where the values belong

Store each token separately under the public repository's
[`anchorpass-production` environment](https://github.com/nazavod777/anchorpass/settings/environments).
The publication job uses that environment's required reviewer gate. The independent
channel signing secret is already a repository Actions secret; registry tokens do
not grant permission to renew signed protocol data.

For remaining or replacement credentials, use the application's dedicated
secret-entry card or enter values directly in GitHub's environment secrets.
Tokens do not belong in source files, release archives, command-line arguments,
workflow inputs or commit messages.

After access is configured, verify the exact reviewed commit and artifact digest,
publish the sealed distributions, read back their public bytes, and install them
from the registries. For each remaining target, registry availability remains
unverified until those checks pass. See the [release runbook](release.md).
