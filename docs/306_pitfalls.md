# 踩过的坑与教训（一行一条：犯了什么 → 该立的纪律）

| 日期 | 坑 | 教训 |
| --- | --- | --- |
| 2026-11-23 | 读 KM 附件型资源（PPT/PDF item/99067 等）时，`resource_outline` 返回「该资源类型不支持章节解析」，`resource_section` 无权限；popo-cli 也没有「下载 attachment 原始文件」命令，导致 PPT 正文拿不到。 | KM 的阅读 API 只支持 blog/page（markdown 章节）；PPT/PDF 等二进制附件要么用户自行转文字，要么只拿得到大纲目录。 |
| 2026-11-23 | 读 KM item/99043 时发现资源数据错位：outline 返回「G67魔神编辑器战斗」目录，但 section 正文却是「音频类型」文章，且正文几乎全是 `<img>` 图片链接、无文字。 | 读 KM 章节前先抽一节验证 `sectionTitle` 是否与 outline 对得上；图文型文章的空正文（全图片）无法文字化，需要用户另提供文字或截图口述。 |
| 2026-11-23 | 帧同步无「等帧/追帧」，客户端本地预推演与服务器 frame 步长错位，导致客户端落后 2 tick、hash 永久不同步。 | 锁步的「预推演步长」必须等于「服务器每个 frame 的逻辑步数」；且新玩家加入/离开要广播快照补齐玩家集合。 |
| 2026-11-23 | 服务器 `_poll_clients` 的 `recv` 未捕获 `ConnectionResetError`，客户端强制 RST 直接把服务器线程带崩。 | io 边界的 recv/send/accept 都要防 OSError（含 ConnectionReset/Aborted），断连走 drop 而非崩溃。 |
