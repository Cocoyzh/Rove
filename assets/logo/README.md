# Rove Logo

Rove Loop — 一条漫游近一圈(差 28°)的醉轨道,玫红渐变的珠子是先头的"笔尖",
即将闭合回路:rove(漫游)收敛为结果。

## 文件

| 文件 | 用途 |
|---|---|
| `logo.svg` / `logo-dark.svg` | 矢量母版(浅色底 / 深色底),任何场景优先引用 |
| `logo-512/256/128/64/32.png` | 浅色版位图,32/64 可直接做 favicon |
| `logo-dark-512/128.png` | 深色版位图 |

## 规格

- 画布 512×512,圆角 116(约 22.7%,对齐 iOS 图标)
- 图形:三瓣扰动圆 `r = 124·(1 + 0.28·sin 3θ)`,开口 28°,线宽 26,圆头
- 珠:半径 18.5,径向渐变 `#FF7A97 → #CE1F4D`
- 环色:墨 `#17161A`(浅色版)/ 象牙 `#FAF7F2`(深色版)
- 底色:象牙 `#FAF7F2` / 墨 `#17161A`

## 用法

- README / 文档:`<img src="assets/logo/logo.svg" width="150" alt="Rove logo" />`
- favicon:`logo-32.png`,或前端引用 `logo.svg` 后声明 `type="image/svg+xml"`
- loading 动画建议:让珠子沿环跑完最后 28° 后归位,天然 spinner
