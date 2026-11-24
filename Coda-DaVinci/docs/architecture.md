# Architecture

`application.generate` owns one episode and prepares its input snapshots. The
router requests a capability assessment grounded in the original task, then applies
a fixed decision rule. Direct generation requires natural motion to be primary,
a risk of rigidity from a proxy, and no need for explicit scene control. Uncertain
or mixed requirements retain Blender. Continued tasks retain their scene workspace.

The root's tools expose file editing, skill loading, Blender execution and rendering,
frame inspection, subject binding, delegation, video generation and acceptance.
Each child owns a workspace and conversation. Asset deliveries are versioned before
being imported into the parent; the root can resume a child with concrete feedback.
Tools run as asynchronous jobs, with explicit wait and interruption controls.

The Blender path uses the packaged modeling policy: coarse structured environments
and props, simple human proxies, identity-color bindings and task-relevant physics.
The root reads the actual rendered proxy before authoring the video prompt. A shot
manifest connects timeline events and subject identities to the reference media.

Video requests preserve revisions and submitted inputs. Generated output passes
technical checks and visual inspection. An independent reviewer receives the
original task, fixed acceptance criteria, candidate video and appearance references.
Its tools allow inspection and a verdict. Three rejected versions exhaust the repair
budget; the root performs the final repair, inspects it and delivers with recorded
remaining issues.

Sequential generation reuses the same team, scene files and subject registry. The
controller supplies the actual final frame of the preceding clip and checks that it
is included with the current proxy. Individual clips retain separate delivery records.

Blender and media subprocesses run through bubblewrap by default. The task workspace
is writable; the host filesystem is mounted read-only, with host GPU devices visible.
For deployment with untrusted instructions, use a dedicated worker/container whose
filesystem contains only the required runtime and task inputs. API secrets are
removed from the subprocess environment. Keep credentials and private data outside
the worker's readable filesystem. Sandbox behavior is implemented in `runtime/sandbox.py`.
