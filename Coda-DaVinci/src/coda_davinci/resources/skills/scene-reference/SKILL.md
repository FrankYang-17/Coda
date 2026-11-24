---
name: scene-reference
description: Generate and inspect scene appearance references for text-to-video work before Blender construction, then bind them to the final video request.
---

# 场景成片参考图

在没有用户提供图像或视频参考的 T2V 任务中使用。先从原始任务整理空间、主要对象、数量、尺度关系和镜头会看到的区域，再调用 `generate_scene_reference` 生成场景图。场景图给 Blender 和视频模型提供成片外观约束，白模继续负责空间、数量、运镜、动作接触和时间状态。

生成提示中写清成片媒介和视觉风格、建筑结构、主要对象的特征轮廓与比例、前中后景层次、重复单元密度、通行区域、材质、粗糙度、反射、透明、光源、色温和接触阴影。任务中的人物或动物及其位置也要出现在场景图中；身份细节继续由 character-appearance 的主体参考图提供。避免只写 cinematic、high quality 之类的空泛形容词。

场景图展示稳定的空间和完成后的视觉质感。转移、拿放、开合、亮灭等随时间变化的状态由白模和逐镜 PE 表达。一个任务有多个明显不同的空间时，每个空间生成一张独立参考图；回访沿用同一空间设计。用户要求外观变化或已检查结果存在具体冲突时，基于原图修订相关属性。

为实际提交的每张图注明空间、外观用途，以及图中可移动物件和开合状态适用的镜头或时间段。逐镜检查参考图是否把已经移走的物件、完成的动作或早期状态带回画面。生成结果出现这种冲突时，由本次会话中的 agent 编辑原有场景参考，保留稳定设计并修正相关状态；新图沿用明确的时段说明。提交时选取所需的图及属性绑定，同一时段的参考应表达相容状态。

先根据任务设计目标场景外观。若使用白模截图说明布局，按任务指定媒介和视觉风格重建目标结构、表面表现和光照，保留截图承担的布局信息。

等待工具完成并查看返回的实际图像。检查主要对象、数量、空间关系、人物位置、结构、材质和灯光；有冲突时用新的 `regenerate=true` 版本修正并重新查看。构造 Blender 白模时读取这张图，把运动和遮挡中需要保持的轮廓、开口、接触面和重复密度落实到几何中。

调用 `video_model` 时把场景参考图的实际 workspace 路径放进 `reference_images`，在 PE 中声明场景图负责稳定的外观、材料和照明，白模视频负责空间、摄像机、动作、接触和事件状态。I2V/V2V 已有用户图像或视频参考时沿用原有输入；后续交互轮复用同一场景参考，保持风格稳定。




## Proxy representation

Represent the environment, props and animals with small assemblies of primitives
that preserve their coarse silhouette and orientation. Include the structures,
landmarks, supports and foreground/background layers needed by the scene. Preserve
requested counts, grouping, occupied space, trajectories, event order and endpoint
states. Use neutral illumination and contact shadows. Keep surface detail simple.

Represent each person with a cylinder torso and sphere head. Move this fixed whole
through the scene and change its facing direction. Invisible logical anchors track
holding, transfer and ownership. Maintain each person's identity, size and occupied
space across shots. Describe fine limb motion in the video prompt and review that
motion in the final video. Apply this representation consistently during repairs.

Record represented attributes, unrepresented requirements, functional exceptions
and entity parts in proxy_capabilities.json. Tag parts with c2v_entity_id and
c2v_part_role when useful. Describe visible geometry and logical state separately.
Inspect the proxy against what it represents and the final video against the
original task, including complete anatomy and natural movement.

## Identity and appearance

Before delegation, assign stable entity_id values, semantic descriptions grounded
in the request, and distinct identity colors (name and hex) to tracked subjects
and props. Share these definitions with children. After inspecting the proxy,
record the observed colors in shot_manifest.entity_map. In the generation prompt,
bind each observed color to the same entity ID and subject description. Bind
reference media using their actual labels. Identity colors serve as lookup markers;
the original request and appearance references determine the finished materials.

## Physics

Drive task-relevant physical processes with executed dynamics calculations or
appropriate simulation. Solve collision, support and nonpenetration for interacting
bodies. Use cloth simulation for required fabric deformation, fluid simulation for
pouring and splashing, particle systems for discrete grains, and gas/smoke simulation
for suspended clouds. Bake the required time interval and render the evaluated
states. Preserve consistent units and propagate physical state through events.

Material deformation may require additional geometry, particles or volumes. Keep
that detail tied to the requested process. Record initial conditions, parameters,
solver settings, cache paths and sampled outcomes in physics_evidence.json. Record
failed or unsupported simulations accurately. The helpers in
scene-assembly/resources/physics.py cover constant gravity and static support;
choose the appropriate solver for more complex interactions.

## Submission and continuation

Read video-pe and write the final generation prompt after inspecting the proxy.
Every Blender-route generation uses the current inspected proxy and its matching
manifest. For continued clips, reuse the saved scene and subject identities, update
the current state, and attach the controller-provided previous final frame.
