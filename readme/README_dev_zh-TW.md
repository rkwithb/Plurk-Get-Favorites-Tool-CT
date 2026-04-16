# 噗浪我的最愛備份工具 CT — 開發者說明

本文檔説明如何從原始碼設定、執行及開發本專案。

---

## 目錄

- [環境需求](#環境需求)
- [環境設定](#環境設定)
- [從原始碼執行](#從原始碼執行)
- [專案結構](#專案結構)
- [主要設計說明](#主要設計說明)
- [授權條款](#授權條款)

---

## 環境需求

### Python

需要 **Python 3.10 以上版本**。

請至 [https://www.python.org](https://www.python.org) 下載安裝。

### tkinter

tkinter 是 Python 標準函式庫的一部分，但在部分 Linux 系統上需要另外安裝。

| 平台 | tkinter 安裝方式 |
|---|---|
| Ubuntu / Debian | `sudo apt install python3-tk` |
| Fedora / RHEL | `sudo dnf install python3-tkinter` |
| macOS | 從 [python.org](https://www.python.org) 安裝 Python，已內含 tkinter |
| Windows | tkinter 已內建於標準 Python 安裝程式中 |


---

## 環境設定

### 1. 複製repo

```bash
git clone https://github.com/rkwithb/Plurk-Get-Favorites-Tool-CT.git
cd Plurk-Get-Favorites-Tool-CT
```

### 2. 建立虛擬環境（建議）

建立虛擬環境可隔離本專案的相依套件，避免與系統或其他專案衝突。

```bash
python -m venv .venv
```

#### 啟動虛擬環境 — Linux / macOS

```bash
source .venv/bin/activate
```

#### 啟動虛擬環境 — Windows

```bash
.venv\Scripts\activate
```

### 3. 安裝相依套件

```bash
pip install -r requirements.txt
```

相依套件包括：
- `customtkinter` — 現代化的 GUI Library，用於建立跨平台桌面應用界面
- `flask` — 輕量級 Web 框架，用於本地 REST API 伺服器
- `requests` — HTTP Request Library
- `plurk-oauth` — Plurk API OAuth 認證
- `python-dotenv` — 環境變數管理，安全存取 `tool.env` 中的認證資訊

### 4. 設定 OAuth 認證

> **備註**：`tool.env` 檔案會在首次啟動 GUI 時自動建立。以下步驟為**可選**，用於希望事先填入認證資訊的開發者。

1. （可選）手動建立 `tool.env`（按照以下格式，將值替換為實際的認證資訊）：

```
PLURK_CONSUMER_KEY=your_consumer_key
PLURK_CONSUMER_SECRET=your_consumer_secret
PLURK_ACCESS_TOKEN=your_access_token
PLURK_ACCESS_TOKEN_SECRET=your_access_token_secret
```

2. 前往 [Plurk API 應用申請頁面](https://www.plurk.com/API)，取得：
   - **Consumer Key**
   - **Consumer Secret**

3. 使用本工具的「**授權登入**」功能取得 **Access Token** 和 **Token Secret**（應用程式首次啟動時會自動提示）。

4. 將上述認證資訊填入 `tool.env` 對應欄位。

**注意**：`tool.env` 檔案包含敏感認證資訊，**不應公開，分享或是上傳至 Git Hub**。已在 `.gitignore` 中排除。

---

## 從原始碼執行

### GUI 模式（推薦用於日常使用）

```bash
python ui/app.py
```

應用程式會啟動圖形界面，你可以透過 UI 進行所有操作（備份、瀏覽、標籤等）。

**首次啟動**：

應用程式會自動建立：
- `config.json` — 儲存語言和 port 設定
- `tool.env` — Plurk API 認證資訊範本（需手動填入金鑰或透過「授權登入」取得）

**其他設定**：
- 預設語言為 `zh_TW`（繁體中文）。可在 UI 右上方語言下拉選單切換。
- REST API 伺服器會在 port `5123` 執行（可在 `config.json` 中修改）。

### CLI 模式（開發者專用）

CLI 模式主要用於腳本化備份流程或測試核心功能，不提供圖形界面。目前 CLI 模式仍在開發中，將透過 `python main.py` 啟動。

**注意**：CLI 用於開發者自行編譯或自動化使用；一般使用者應使用 GUI 模式。

---

## 專案結構

```
Plurk-Get-Favorites-Tool-CT/
├── README.md                        # 主文檔
├── requirements.txt                 # Python 依賴列表
├── config.json                      # 語言和 port 設定（首次執行時自動生成）
├── tool.env                         # OAuth 認證資訊（已在.gitignore排除
│
├── ui/
│   ├── __init__.py
│   └── app.py                       # GUI 入口點（customtkinter GUI）
│
├── core/
│   ├── __init__.py
│   ├── auth.py                      # OAuth 認證邏輯
│   ├── db.py                        # SQLite 資料庫操作
│   ├── backup.py                    # 備份流程協調
   ├── export.py                    # 匯出備份資料為月份 JS 檔案
│   ├── server.py                    # Flask REST API 伺服器
│   ├── config.py                    # 配置管理
│   ├── i18n.py                      # 多語言化 — 載入翻譯檔案
│   ├── logger.py                    # 日誌記錄
│   ├── paths.py                     # 路徑管理
│   └── version.py                   # 版本資訊
│
├── locales/
│   ├── zh_TW.json                   # 繁體中文翻譯
│   └── en.json                      # 英文翻譯（後續加入）
│
├── readme/
│   ├── README_user_zh-TW.md         # 使用者指南（中文）
│   ├── README_dev_zh-TW.md          # 開發者指南（中文）
│   ├── README_user_en.md            # 使用者指南（英文，待補充）
│   └── README_dev_en.md             # 開發者指南（英文，待補充）
│
├── tests/                           # 測試檔案
│   ├── test_*.py
│   └── ...
│
├── log/                            # 執行日誌（已在.gitignore排除
│   └── *.log
│
└── .gitignore                       # Git 忽略規則
```

### 核心模組說明

| 模組 | 功能 |
|---|---|
| `core/auth.py` | 處理 OAuth 2.0 認證流程，管理 access token 生命週期 |
| `core/db.py` | SQLite 資料庫操作，管理噗文、標籤、 資料庫遷移 |
| `core/backup.py` | 備份流程協調，支援「更新現有備份」/「指定日期」/「完整備份」三種模式 |
| `core/export.py` | 匯出備份資料為月份 JS 檔案 |
| `core/server.py` | Flask REST API 伺服器，提供前端（網頁瀏覽）溝通介面 |
| `core/i18n.py` | 多語言系統，管理多語言翻譯檔案 |
| `core/logger.py` | 日誌記錄系統，支援LineBuffer磁碟寫入確保資料完整 |
| `ui/app.py` | customtkinter GUI 主應用程式 |

---

## 主要設計說明

### 架構簡介

本工具採用多層設計：

1. **GUI 層（ui/app.py）** — customtkinter 圖形界面
2. **API 層（core/server.py）** — Flask REST API 伺服器，連接 GUI 和核心邏輯
3. **業務邏輯層（core/*.py）** — 認證、備份、資料庫操作等
4. **資料層（core/db.py）** — SQLite 資料庫

### REST API 與前端溝通

應用程式啟動時，GUI 會同時啟動一個本地 Flask REST API 伺服器（預設在 `localhost:5123`，可在 `config.json` 中修改）。GUI 透過 API 調用核心功能：

- `GET /api/plurks` — 列出已備份的噗文
- `GET /api/tags` — 查詢標籤
- `POST /api/tags` — 新增標籤
- `DELETE /api/tags` — 刪除標籤
- 其他端點詳見 `core/server.py`

此設計便於後續開發網頁前端或其他客戶端。

### 備份流程

使用者點選「**▶  開始備份**」後的流程：

```
使用者點選「▶  開始備份」
    ↓
GUI 收集備份模式選擇（「更新現有備份」/「指定日期」/「完整備份」）
    ↓
core/backup.py 協調準備備份流程
    ↓
依據使用者選擇的備份模式選擇對應策略
    ↓
迴圈呼叫 Plurk API 逐頁查詢噗文（第一次 API 呼叫時隱含驗證 Access Token）
    ↓
檢查 SQLite 資料庫避免重複備份
    ↓
將新噗文寫入 SQLite 資料庫 (core/db.py)
    ↓
匯出備份資料到月份 JS 檔案 (core/export.py)
    ↓
計算統計資訊（本次新增、已儲存總數等）並返回給 GUI
    ↓
GUI 在日誌區域顯示統計情報
```

### 多語言支援

所有 UI 字串透過 `core/i18n.py` 的 `t("key")` 函式讀取，key 對應至 `locales/zh_TW.json` 等翻譯檔案。

- 切換語言時在 `config.json` 儲存選擇
- GUI 重啟時自動載入新語言

如需新增語言，只需在 `locales/` 新增翻譯 JSON 檔案（例如 `en.json`）。

### 資料庫設計

SQLite 資料庫儲存：
- **favorites** 表 — 噗文（plurk_id、content_raw、posted、posted2、owner_id、nick_name、plurk_type、raw_json 等）
- **tags** 表 — 標籤名稱（id、name，name 設為 UNIQUE 以避免重複）
- **plurk_tags** 表 — 噗文與標籤的關聯（多對多關係）
- 資料庫自動遷移：首次使用時自動偵測並升級舊版資料庫結構，支援增量欄位補全和資料回填

### 日誌系統

`core/logger.py` 為單例模式，使用 line-buffered 檔案 I/O（`buffering=1`）確保日誌即時寫入磁碟。這樣即使程式意外崩潰，仍能保留完整日誌用於除錯。

**日誌保留管理**：系統會自動保留最近 20 個會話日誌檔案（`MAX_SESSION_LOGS = 20`），超過此數量時自動刪除最舊的日誌，避免磁碟空間被佔滿。

---

## 授權條款

本專案採用 [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) 授權，**僅限非商業使用**。

- ✅ 個人使用、學習、研究、修改
- ✅ 非營利組織使用
- ❌ 商業用途（包括販售、商業服務等）
- ❌ 未經授權的發行或販售

> **免責聲明**：使用風險自負。作者不對任何損失或損害負責。
