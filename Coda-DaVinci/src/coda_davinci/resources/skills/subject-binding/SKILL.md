---
name: subject-binding
description: Establish and reuse stable identities, outfit versions and single-view or character-sheet references across scenes and child agents. The harness resolves identity anchors and per-shot image bindings automatically.
---

# 跨镜头主体绑定

在搭建重复出现的人物／动物前，由主 agent 登记主体；子 agent 通过 get_subject 沿用登记结果，不重新设计身份。人物外观参考制作使用 character-appearance 技能。

## 登记与引用

- register_subject 创建稳定的小写 subject_id、person/animal、identity_caption 和 outfit_id/outfit_caption。
- 身份描述与服装分开；表情、动作、湿润和持物等只记录在镜头状态。
- 首次 bind_subject_reference 会保存身份参考；后续换装或替换某版外观图不自动覆盖它。主 agent 查看新图后，可用 select_subject_identity_reference 显式选定新的身份参考版本，旧版本留档。
- 绑定已有图时声明真实 layout：single 或 character-sheet。设计板里的多个视角必须属于同一人物；不同人物应分别登记。
- get_subject 返回当前身份参考、各服装参考、布局、哈希和版本，并复制到当前 agent 的 inputs/subjects。
- 更新档案或绑定图时使用最新 revision；不要覆盖其他 agent 刚提交的版本。

## 生成外观

generate_subject_reference 优先读取 GPT_IMAGE_MODEL（标准 images.generate / images.edit），未配置时回退 IMAGE_MODEL。
默认人物生成设计板、动物生成单视图；可显式指定布局。无 reference_path 时自动使用当前服装参考或稳定身份参考进行编辑。新服装也沿用同一身份。
已有图默认缓存复用；只有明确需要更新时才 regenerate=true。格式不匹配会返回 layout_matches=false，不自动收费重生。
工具返回后台 job_id 时收取原 job；未确认的提交错误不能通过重复调用解决。

## 场景与镜头

Blender 主体 collection 或根对象添加 subject_id。最终 manifest 的 entity_id / visible_entity_ids 使用相同 ID。
主体离场后再出现仍用原 ID。每个 outfit_id 只表示一种服装／皮毛版本，不把临时动作写进去。
有多个版本时，每个出现镜头明确：
```json
"subject_states": {"person_a": {"outfit_id": "formal", "state": "外套淋湿，右手拿箱子"}}
```
只有一个服装版本时可省略 outfit_id；无状态变化时省略 state。

注册主体的 reference_bindings 由 harness 根据档案重建；不要复制以前某次请求的图片编号。
主智能体读取 video-pe 编写最终生成提示词；video_model 根据整片镜头清单向视频生成提供稳定身份图、当前服装图、布局、哈希和镜头状态。
多个镜头使用相同文件时自动去重；身份图与服装图可以是同一文件。
设计板视图是同一个人，不是多个演员。分栏、标签、灰底及参考姿势不属于最终画面。

绑定保证输入对应关系一致，不能保证生成结果一定一致。图像或视频服务拒绝素材时保留失败，不删除必要参考后伪装为绑定成功。

当旧单视图已被选定的新设计板替换，harness 不会再为身份用途自动发送旧图；如果原图还有构图或场景作用，在 manifest 的 reference_bindings 中明确 composition/environment 等用途，程序会保留。
