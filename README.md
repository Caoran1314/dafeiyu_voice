# 大肥鱼音色

只做一件事：把她的文字回复换成**你自己的声音**（GPT-SoVITS 本地合成）。

## 依赖

- GPT-SoVITS 服务（默认 `api_v2.py`，端口 9880）
- 每个情绪一个参考音频目录

## 目录结构

```
data/refaudio/
├── 平静/ref.wav + asr.txt
├── 高兴/ref.wav + asr.txt
├── 害羞/ref.wav + asr.txt
├── 生气/ref.wav + asr.txt
└── 着急/ref.wav + asr.txt
```

`ref.wav` 要 5–10 秒干净人声，`asr.txt` 是这段音频说的话。

## 配置

| 键 | 默认 | 说明 |
|---|---|---|
| `enable` | false | 装好后要手动打开 |
| `prob` | 0.25 | 多大比例的回复转语音 |
| `api_base` | http://172.17.0.1:9880 | SoVITS 地址 |

## 小技巧

用户明确说"发条语音""想听你说"时，会**强制**合成，不会出现"说要发语音却发文字"的自相矛盾。
