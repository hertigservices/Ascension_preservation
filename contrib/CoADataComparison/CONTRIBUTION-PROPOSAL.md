# Proposal: reproducible CoA coverage comparison, without redistributing client content

Status: draft pull request for maintainer review; not merged.

Baseline: hertigservices/Ascension_preservation at `664f2768f0695187349fc38842eae6a1588cc352`.

## Proposed first contribution

A standalone, standard-library Python comparison tool plus synthetic tests. Collaborators supply their own local CA export and archived CoA Build Hub data. The tool reports source hashes, identifier overlap, description availability and community-versus-runtime dependency differences without copying descriptions, HTML tooltips, icons, client files, usernames, or machine-local paths into its report.

This is an evidence comparison, not an importer. It never rewrites the engine CA export or substitutes community dependencies for runtime ConnectedNodes. The revision argument is labeled a caller assertion; content hashes bind the files actually read.

Source references:
- Preservation engine schema: https://github.com/hertigservices/Ascension_preservation/blob/664f2768f0695187349fc38842eae6a1588cc352/data/ca-export/SCHEMA.md
- Community source: https://coabuildhub.com
- Skill source recorded by the archived Hub README: https://db.ascension.gg
- Hub archive date recorded by that README: 2026-07-31. This is archive provenance, not a fresh crawl or validation of current site terms.

## Verified baseline findings motivating the proposal

| Comparison | Result |
|---|---:|
| Hub master unique spell IDs | 5,172 |
| IDs present in any of five CA rank fields | 3,601 |
| IDs absent from those CA fields | 1,571 |
| CA rows lacking both descriptions with a nonempty Hub description by primary spell ID | 3,600 |
| Community dependency references matching runtime edges | 4,809 |
| Community dependency references mapped to candidate differences | 142 |
| Candidate differences remaining inside the same CA class/tab | 134 |
| Unmapped or ambiguous community references | 43 |
| Dangling community node references | 8 |

These were independently recomputed during the initial audit. The new tool must reproduce them before release; its test and real-corpus receipts are recorded separately.

## Interpretation boundaries

- The 1,571 IDs are absent from the advancement export, not necessarily from the client. Classify them as supporting/trigger, historical/versioned, advancement candidate, or unresolved before considering import.
- The 3,600 joins establish description availability, not correct current wording or permission to copy it. They remain proposals until version, class context, source attribution and rights are reviewed.
- Dependency joins use uniquely mapped primary spell IDs. Matching IDs alone do not establish semantic equivalence. Eight candidate differences cross class/tab boundaries; even the 134 same-boundary candidates need review.
- Runtime edges retain stronger runtime provenance than a community web representation. No authoritative edges are added by this contribution.
- Local client snapshots checked during research do not contain the existing dangling targets 6451, 7181 or 17567. This proposal does not claim to recover them.

## Suggested review order

1. Review the tool, synthetic fixtures, output schema and absence of client-content redistribution.
2. Reproduce it locally using the maintainer's own data, retaining source hashes.
3. Review a small sample of ID-level classifications before any description-content proposal.
4. Consider a separate narrow graph-import validation fix; keep it independent of data enrichment.
5. Discuss the availability of historical DBC/world-cache inventories separately. Do not attach raw datasets to a public issue or PR.

## Separate reproducibility follow-ups

The initial audit identified original-machine data paths, sibling-module import assumptions, obsolete offline-test calls and stale integration packet-order assumptions. Those require isolated fixtures before server-related tests can safely run. They are not silently bundled into the comparison tool or treated as already repaired.

The tool does not attempt authentication, client-memory reads, packet capture, network interception, database imports, or server startup. No new dependency or paid service is required.

## Publication boundary

This draft includes aggregate findings and original analysis only. It makes no claim that publicly accessible community text has unrestricted redistribution rights. The candidate package must exclude raw client/Hub data, generated real-corpus reports, private inventories and internal execution logs. Submission should retain the upstream license/notice for any included upstream-derived patch.

