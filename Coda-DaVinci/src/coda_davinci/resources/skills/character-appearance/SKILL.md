---
name: character-appearance
description: Design stable human appearance references for multi-shot video using cinematic character sheets, existing identity anchors and outfit-specific image edits. Use with subject-binding; this skill does not build proxy motion or replace the user's visual style.
---

# 人物外观设计与角色设计板

目标是让同一人物在不同场景、角度或服装下仍有统一的外观依据。设计板用于描述身份，不是视频的构图模板，也不保证视频服务一定接受素材。

## 先明确身份与服装

主 agent 用 subject-binding 登记人物和服装版本。外观子 agent 先 get_subject，沿用已有 ID、caption 和参考，不创建另一个“差不多的人”。

identity_caption 写稳定的脸部、发型和体型特征；服装、配饰属于 outfit_caption；表情、动作、湿润和持物属于镜头状态。
设计板中的外观细节用于最终视频生成。Blender 人物几何遵循 character-animal-proxy，按镜头距离、轮廓和动作需要确定细节，外观图与动作白模分别提供身份外表和空间事件信息。
保留用户明确指定的细节。衬衫与圆领上衣不是同一种服装：若用户指定领口、纽扣、袖长等，caption 应明确，不用模糊词替代。
未指定的设计选择保持简洁且前后一致，不随每个镜头重新扩写身份。

## 参考资产的选择

- 已有可信的人物图：查看并绑定到对应主体；基于它制作设计板，保留脸、体型及用户要求的外观。
- 已有设计板：bind_subject_reference(..., layout="character-sheet")，不要为了格式再生一张。
- 没有参考：generate_subject_reference(..., layout="character-sheet") 从登记的 caption 生成。
- 用户明确只需单视图或要求直接使用原图时，选择 layout="single"，不强制派生设计板。
- 若未配置生图服务且用户没有要求生成图片，可仅登记 caption 并明确没有图像绑定；用户明确要求设计板时报告配置缺失，不编造图片路径。

人物生成工具的默认布局是 character-sheet，动物仍默认 single。
已有图片不会因默认布局变化被偷偷替换。若工具返回 cached=true 且 layout_matches=false，确认需要派生新布局后，显式 regenerate=true；reference_path=null 会自动使用已有图片作为编辑输入。
主 agent 查看并认可新设计板后，用 select_subject_identity_reference(subject_id, outfit_id, expected_revision) 将其选为后续身份依据。旧图留在历史中，不再作为过期身份图自动重复提交。

## 电影角色设计板

同一份板对应一个人物、一个服装版本：

- 正面、三分之四、侧面、背面的全身视图；保持身高比例、发型和衣服一致。
- 清晰可读的中性、微笑、专注等面部参考；按角色需求选择，不需要堆满表情。
- 服装剪裁、材质和鞋子的必要细节，简洁的视图标签与背景。

保持人脸可见、视图比例合理。不要把不同角色或多套服装混在同一张板里。最终视频应按镜头完成动作，而非摆出设计板里的姿势。
设计板应完整表达角色的外表、服装和表情，保留清晰可见的面部及真实的素材来源说明。

## 视频服务返回 PrivacyInformation

`InputImageSensitiveContentDetected.PrivacyInformation` 表示服务方认为传入图片可能含有真人。收到该错误后，检查该次提交的图片快照，找出涉及的人物图、身份图或服装图。

将相关外观图重新组织成角色介绍板：同一角色的正面、侧面、背面三视图，配上服装剪裁、材质细节和少量表情演示，使用简洁背景、清晰分栏和视图标签。对于生成的虚构角色，也可采用插画或三维角色设定图的表现方式，保持身份特征、衣着和目标成片风格。

通过 `generate_subject_reference` 制作时使用 `layout="character-sheet"`、`regenerate=true`，在提示词中明确上述布局。检查新图后绑定到原 subject_id/outfit_id，主 agent 更新选定的身份参考及显式引用路径，再以新视频 revision 提交。每次重试都应对应已检查的参考图修改。

## 换装与迭代

先由主 agent 为同一 subject_id 注册新 outfit_id，再生成该版本的参考。reference_path=null 会自动沿用初始身份参考，通过标准图片编辑接口换装，而不是从文字重生另一张脸。
若需要显式指定另一个已认可视角作为编辑依据，可传当前工作空间的 reference_path。
输出参考图后查看它：人物、服装是否符合任务，多个面板是否仍是同一设定。这里是正常资产制作检查，不读取实验的独立评分规则。

需要修订时保留旧文件和版本；harness 会记录历史、来源哈希与布局。收到不明确的提交超时，等待/检查既有 job，不重复生成。等待图片服务时可先完成独立建模工作；无其他工作时 wait_tool 的 yield_time_ms 用 60000，不要每秒轮询。

## 交付

交付 subject_id、outfit_id、当前 revision、设计板路径及尚未解决的外观偏差；主 agent 和建模子 agent 用 get_subject 获取同一份资产。
不要求把面部和服装细节建进 Blender 白模。最终图片编号、身份图与服装图的对应关系由 harness 编排，不手写跨请求固定的“图片1”。




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
