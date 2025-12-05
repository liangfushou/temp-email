# ClawCloud 部署指南

## 問題診斷

如果遇到 `ImagePullBackOff` 錯誤，但本地可以正常拉取映像，通常是 ClawCloud 配置問題。

### 確認映像可訪問性

本地測試（已驗證可行）：
```bash
docker pull ghcr.io/tonnywong1052/temp-email:latest
# 成功拉取，Digest: sha256:074e481782167c51fe56912fd73f84e9eab2410c5af22513dc55650d0a17cd17
```

---

## 🎯 解決方案

### 方案 1：使用 Digest 替代 Tag（強烈推薦）

在 ClawCloud 部署配置中，使用完整的 digest 而不是 `latest` 標籤：

```
ghcr.io/tonnywong1052/temp-email@sha256:074e481782167c51fe56912fd73f84e9eab2410c5af22513dc55650d0a17cd17
```

**為什麼這樣做？**
- 避免標籤解析問題
- 確保使用精確的映像版本
- 繞過可能的快取問題

---

### 方案 2：檢查映像名稱格式

⚠️ **常見錯誤**：映像名稱中有隱藏的空格或換行

從錯誤訊息 `"ghcr.io/tonnywong10 52/temp-email:latest"` 看出可能有：
- 用戶名中間的空格
- 複製貼上時的換行符

**正確格式**（請完整複製以下內容）：
```
ghcr.io/tonnywong1052/temp-email:latest
```

**檢查清單**：
- [ ] 確認沒有多餘空格
- [ ] 用戶名是 `tonnywong1052` (全小寫，無空格)
- [ ] registry 是 `ghcr.io` (不是 `docker.io` 或其他)

---

### 方案 3：使用 Kubernetes YAML 配置

如果 ClawCloud 支援 YAML 配置，使用以下配置：

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: temp-email
  labels:
    app: temp-email
spec:
  replicas: 1
  selector:
    matchLabels:
      app: temp-email
  template:
    metadata:
      labels:
        app: temp-email
    spec:
      containers:
        - name: temp-email
          # 方式 A: 使用 digest（推薦）
          image: ghcr.io/tonnywong1052/temp-email@sha256:074e481782167c51fe56912fd73f84e9eab2410c5af22513dc55650d0a17cd17

          # 方式 B: 使用 tag
          # image: ghcr.io/tonnywong1052/temp-email:latest

          # 設定映像拉取策略
          imagePullPolicy: Always

          ports:
            - name: http
              containerPort: 1234
              protocol: TCP

          env:
            - name: PORT
              value: "1234"
            - name: HOST
              value: "0.0.0.0"
            - name: EMAIL_TTL
              value: "3600"
            - name: MAIL_CHECK_INTERVAL
              value: "10"

          resources:
            requests:
              memory: "256Mi"
              cpu: "250m"
            limits:
              memory: "512Mi"
              cpu: "500m"

          livenessProbe:
            httpGet:
              path: /api/health
              port: 1234
            initialDelaySeconds: 10
            periodSeconds: 30
            timeoutSeconds: 5

          readinessProbe:
            httpGet:
              path: /api/health
              port: 1234
            initialDelaySeconds: 5
            periodSeconds: 10
            timeoutSeconds: 3

---
apiVersion: v1
kind: Service
metadata:
  name: temp-email-service
spec:
  type: LoadBalancer
  ports:
    - port: 80
      targetPort: 1234
      name: http
  selector:
    app: temp-email
```

---

### 方案 4：ClawCloud Web UI 配置

如果使用 ClawCloud 的 Web 界面部署，請按以下步驟操作：

1. **創建新應用**
   - 應用名稱：`temp-email`
   - 部署方式：選擇「容器映像」

2. **容器配置**

   **映像地址**（選擇其一）：
   ```
   # 選項 A：使用 digest（最可靠）
   ghcr.io/tonnywong1052/temp-email@sha256:074e481782167c51fe56912fd73f84e9eab2410c5af22513dc55650d0a17cd17

   # 選項 B：使用 latest tag
   ghcr.io/tonnywong1052/temp-email:latest
   ```

   **端口配置**：
   - 容器端口：`1234`
   - 服務端口：`80` 或 `1234`
   - 協議：`HTTP`

3. **環境變數**（可選）
   ```
   PORT=1234
   HOST=0.0.0.0
   EMAIL_TTL=3600
   MAIL_CHECK_INTERVAL=10
   ```

4. **資源配置**
   - CPU：0.25 核心 (250m)
   - 記憶體：256MB (最低) / 512MB (推薦)

5. **健康檢查**
   - 健康檢查路徑：`/api/health`
   - 端口：`1234`
   - 初始延遲：10 秒

---

## 🔧 進階排查

### 1. 查看 ClawCloud Pod 日誌

如果有 kubectl 訪問權限：

```bash
# 查看 Pod 狀態
kubectl get pods -l app=temp-email

# 查看詳細錯誤
kubectl describe pod <pod-name>

# 查看映像拉取事件
kubectl get events --sort-by='.lastTimestamp' | grep -i pull
```

### 2. 驗證 ClawCloud 的 Registry 連通性

在 ClawCloud 的某個 Pod 中測試：

```bash
# 進入任意 Pod
kubectl run test-pull --rm -it --image=alpine -- sh

# 測試網絡連通性
apk add curl
curl -I https://ghcr.io

# 測試 DNS 解析
nslookup ghcr.io
```

### 3. 檢查 ClawCloud 的 ImagePullPolicy

確保 `imagePullPolicy` 設定正確：
- `Always`：每次都拉取（推薦用於 latest 標籤）
- `IfNotPresent`：本地不存在時才拉取
- `Never`：從不拉取

對於公開映像，使用 `Always` 或 `IfNotPresent` 都可以。

---

## 📋 快速檢查清單

完成以下檢查以確保部署成功：

- [ ] 映像名稱無空格：`ghcr.io/tonnywong1052/temp-email:latest`
- [ ] 本地可以拉取映像（已驗證✓）
- [ ] ClawCloud 配置中的映像名稱與本地完全一致
- [ ] 嘗試使用 digest 而不是 latest 標籤
- [ ] 容器端口設為 1234
- [ ] 環境變數配置正確（如需要）
- [ ] 健康檢查路徑為 `/api/health`
- [ ] 資源限制合理（至少 256MB 記憶體）

---

## 🆘 如果仍然失敗

1. **截圖 ClawCloud 的錯誤訊息**
   - Pod 的完整錯誤訊息
   - Events 中的拉取失敗詳情

2. **檢查 ClawCloud 是否需要特殊配置**
   - 某些平台可能需要設定 registry mirrors
   - 某些區域可能有網絡限制

3. **聯繫 ClawCloud 支援**
   - 提供映像 URL：`ghcr.io/tonnywong1052/temp-email:latest`
   - 說明本地可以拉取但平台無法拉取
   - 詢問是否有 registry 白名單或防火牆設定

---

## 📦 替代部署方案

如果 ClawCloud 持續無法拉取 GHCR 映像，可以考慮：

### 方案 A：推送到其他 Registry

```bash
# 拉取 GHCR 映像
docker pull ghcr.io/tonnywong1052/temp-email:latest

# 重新標記到其他 registry（例如 Docker Hub）
docker tag ghcr.io/tonnywong1052/temp-email:latest tonnywong1052/temp-email:latest

# 推送
docker push tonnywong1052/temp-email:latest
```

### 方案 B：使用 ClawCloud 的內建 Registry

如果 ClawCloud 提供內建 registry，可以推送到那裡：

```bash
# 假設 ClawCloud registry 為 registry.clawcloud.com/your-namespace
docker tag ghcr.io/tonnywong1052/temp-email:latest registry.clawcloud.com/your-namespace/temp-email:latest
docker push registry.clawcloud.com/your-namespace/temp-email:latest
```

---

## ✅ 成功部署後的驗證

部署成功後，訪問以下端點驗證：

```bash
# 健康檢查
curl http://<your-clawcloud-domain>/api/health

# 獲取可用域名
curl http://<your-clawcloud-domain>/api/domains

# 測試生成臨時郵箱
curl -X POST http://<your-clawcloud-domain>/api/email/generate
```

預期回應：
```json
{
  "email": "xxxx@example.com",
  "token": "xxxxx",
  "expires_at": "2025-11-05T12:00:00Z"
}
```

---

## 📚 相關資源

- GitHub Repository: https://github.com/TonnyWong1052/temp-email
- GHCR Package: https://github.com/users/TonnyWong1052/packages/container/temp-email
- Dockerfile: `/Dockerfile`
- GitHub Actions Workflow: `/.github/workflows/deploy.yml`
