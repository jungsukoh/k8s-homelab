# K8s 홈 클러스터 실습 가이드 (완전판 v3)
## GitHub 세팅 → ArgoCD + Jenkins CI/CD + Prometheus/Alertmanager

---

# ━━━ 전체 아키텍처 흐름 ━━━

```
[로컬 PC (Windows)]
  코드 수정 → git push
       ↓ Webhook (또는 2분 폴링)
[Jenkins Pod - jenkins ns]
  체크아웃 → Docker 빌드 → Harbor push → manifest 태그 업데이트 → Git push
       ↓ Git 변경 감지 (3분 폴링)
[ArgoCD Pod - argocd ns]
  manifest 변경 감지 → K8s 자동 배포 (Rolling / Blue-Green / Canary)
       ↓
[my-app Pod - my-app ns]
  실제 앱 실행 → /metrics 노출
       ↓
[Prometheus - monitoring ns]
  ServiceMonitor → 메트릭 수집 → Alert Rule 평가
       ↓ 조건 충족
[Alertmanager - monitoring ns]
  → Slack 알림 / 이메일 알림
  Jenkins 빌드 결과도 → Slack
```

**노드 구성**
```
master-node  10.0.0.10    K8s 컨트롤플레인 + 모든 kubectl/helm/argocd 명령 실행
worker-1     10.0.0.11    워크로드 실행
worker-2     10.0.0.12    워크로드 실행
Harbor       10.0.0.210   컨테이너 레지스트리 (MetalLB EXTERNAL-IP)
```

> ⚠️ 특별한 언급 없으면 모든 명령은 **master-node** 에서 실행
> 접속: `ssh ubuntu@10.0.0.10`

---

# ━━━ STEP 0: GitHub 초기 세팅 ━━━

> 로컬 PC (Windows) 에서 실행

## 0-1. Git 초기 설정

```powershell
# PowerShell
git --version
# 없으면: winget install Git.Git

git config --global user.name "your-name"
git config --global user.email "your-email@gmail.com"
git config --global core.autocrlf false
```

## 0-2. GitHub 레포 생성

1. https://github.com → `+` → **New repository**
2. 설정:
   ```
   Repository name : my-k8s-app
   Visibility      : Public   ← ArgoCD 인증 없이 접근하려면 Public
   Initialize      : ✅ Add a README file
   ```
3. **Create repository**

## 0-3. GitHub Personal Access Token (PAT) 발급

Jenkins가 manifest 업데이트 후 GitHub에 push하기 위해 필요.

1. GitHub → 프로필 → **Settings**
2. 좌측 하단 **Developer settings**
3. **Personal access tokens → Tokens (classic)**
4. **Generate new token (classic)**
5. 설정:
   ```
   Note       : jenkins-k8s
   Expiration : 90 days
   Scopes     : ✅ repo (전체)
   ```
6. **Generate token** → 발급된 토큰 반드시 복사 (재확인 불가)

## 0-4. 로컬에 레포 클론 및 파일 구조 생성

```powershell
# PowerShell
cd C:\Users\<your-name>\Projects
git clone https://github.com/<YOUR_ID>/my-k8s-app
cd my-k8s-app
```

아래 구조로 파일 전부 생성:
```
my-k8s-app/
├── app/
│   ├── main.py
│   └── requirements.txt
├── Dockerfile
├── Jenkinsfile
└── k8s/
    ├── namespace.yaml
    ├── deployment.yaml
    ├── service.yaml
    └── ingress.yaml
```

**app/requirements.txt**
```
flask
prometheus_client
```

**app/main.py**
```python
from flask import Flask
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
import os, time

app = Flask(__name__)
VERSION = os.getenv("APP_VERSION", "v1")

REQUEST_COUNT   = Counter('http_requests_total', 'Total HTTP requests', ['method', 'endpoint', 'status'])
REQUEST_LATENCY = Histogram('http_request_duration_seconds', 'HTTP request latency', ['endpoint'])

@app.route('/')
def hello():
    start = time.time()
    REQUEST_COUNT.labels(method='GET', endpoint='/', status='200').inc()
    REQUEST_LATENCY.labels(endpoint='/').observe(time.time() - start)
    return f"Hello from {VERSION}!\n"

@app.route('/health')
def health():
    return "ok", 200

@app.route('/metrics')
def metrics():
    return generate_latest(), 200, {'Content-Type': CONTENT_TYPE_LATEST}

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
```

**Dockerfile**
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ .
EXPOSE 5000
CMD ["python", "main.py"]
```

**k8s/namespace.yaml**
```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: my-app
```

**k8s/deployment.yaml**
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-app
  namespace: my-app
spec:
  replicas: 2
  selector:
    matchLabels:
      app: my-app
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  template:
    metadata:
      labels:
        app: my-app
    spec:
      containers:
      - name: my-app
        image: 10.0.0.210/library/my-app:v1
        ports:
        - containerPort: 5000
        env:
        - name: APP_VERSION
          value: "v1"
        readinessProbe:
          httpGet:
            path: /health
            port: 5000
          initialDelaySeconds: 5
          periodSeconds: 5
        livenessProbe:
          httpGet:
            path: /health
            port: 5000
          initialDelaySeconds: 10
          periodSeconds: 10
```

**k8s/service.yaml**
```yaml
apiVersion: v1
kind: Service
metadata:
  name: my-app
  namespace: my-app
  labels:
    app: my-app
spec:
  selector:
    app: my-app
  ports:
  - name: http
    port: 80
    targetPort: 5000
```

**k8s/ingress.yaml**
```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: my-app
  namespace: my-app
spec:
  ingressClassName: nginx
  rules:
  - host: my-app.local
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: my-app
            port:
              number: 80
```

**Jenkinsfile** (일단 placeholder, CHAPTER 2에서 내용 채움)
```groovy
pipeline {
    agent any
    stages {
        stage('placeholder') { steps { echo 'TODO' } }
    }
}
```

## 0-5. 초기 커밋 및 Push

```powershell
git add .
git commit -m "init: project structure"
git push origin main
```

---

# ━━━ STEP 1: worker 노드 사전 설정 ━━━

> worker-1, worker-2 **각각** SSH 접속 후 실행

## 1-1. Docker 설치 (Jenkins 빌드용)

```bash
# worker-1 접속 (터미널 1)
ssh ubuntu@10.0.0.11

# worker-2 접속 (터미널 2)
ssh ubuntu@10.0.0.12
```

```bash
# worker-1, worker-2 각각 아래 명령 실행

# 기존 충돌 패키지 제거
sudo apt-get remove -y docker docker-engine docker.io containerd runc 2>/dev/null || true
sudo apt-get autoremove -y
sudo apt-get autoclean

# Docker 공식 repo 추가 (docker.io 대신 docker-ce 사용 → 패키지 충돌 방지)
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli

# ⚠️ Docker 설치 후 containerd 설정 덮어쓰기 방지 → K8s용 설정 재적용
sudo containerd config default | sudo tee /etc/containerd/config.toml
sudo sed -i 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml
sudo systemctl restart containerd

# Docker 서비스 시작
sudo systemctl enable docker
sudo systemctl start docker

# ubuntu 계정 docker 권한
sudo usermod -aG docker ubuntu
sudo chmod 666 /var/run/docker.sock

# 확인
docker version
sudo systemctl status containerd   # active (running) 확인
```

## 1-2. Harbor HTTP 접근 설정

Harbor가 TLS 없이 HTTP로 구성된 경우.

```bash
# worker-1, worker-2 각각 실행

# Docker insecure-registries 설정
sudo tee /etc/docker/daemon.json <<'EOF'
{
  "insecure-registries": ["10.0.0.210"]
}
EOF
sudo systemctl restart docker

# containerd config.toml 에 Harbor HTTP 미러 추가
sudo nano /etc/containerd/config.toml
```

`config.toml` 파일 끝에 아래 내용 추가:
```toml
[plugins."io.containerd.grpc.v1.cri".registry.mirrors."10.0.0.210"]
  endpoint = ["http://10.0.0.210"]

[plugins."io.containerd.grpc.v1.cri".registry.configs."10.0.0.210".tls]
  insecure_skip_verify = true
```

```bash
sudo systemctl restart containerd

# 확인
sudo systemctl status containerd
sudo systemctl status docker
```

## 1-3. master-node /etc/hosts 등록

> Windows hosts 파일 편집 없이, master-node에서 도메인으로 접근 가능하게 설정.

```bash
# master-node 에서 실행
ssh ubuntu@10.0.0.10

# Ingress-Nginx EXTERNAL-IP 확인
kubectl get svc -n ingress-nginx
# EXTERNAL-IP 컬럼 값 메모 (예: 10.0.0.200)

# master-node /etc/hosts 에 등록
sudo tee -a /etc/hosts <<'EOF'

# K8s 서비스
10.0.0.200  my-app.local
10.0.0.200  argocd.local
10.0.0.200  jenkins.local
10.0.0.200  grafana.local
EOF

# 확인
cat /etc/hosts | grep local
curl http://my-app.local     # 앱 배포 후 테스트용
```

> ⚠️ Ingress-Nginx EXTERNAL-IP가 다르면 위 `10.0.0.200` 부분을 실제 IP로 수정.
> 이후 모든 `curl` 테스트는 master-node에서 실행.

---

# ━━━ CHAPTER 1: ArgoCD 실습 ━━━

> 모든 명령: **master-node**

## 1-1. ArgoCD CLI 설치

```bash
# master-node
curl -sSL -o argocd https://github.com/argoproj/argo-cd/releases/latest/download/argocd-linux-amd64
sudo install -m 555 argocd /usr/local/bin/argocd
argocd version --client
```

## 1-2. ArgoCD 로그인

```bash
# master-node

# EXTERNAL-IP 확인
kubectl get svc argocd-server -n argocd
# 예: 10.0.0.201

# 초기 admin 비밀번호 확인
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath="{.data.password}" | base64 -d && echo

# CLI 로그인 (EXTERNAL-IP 사용)
argocd login 10.0.0.201 --username admin --insecure
```

## 1-3. ArgoCD Application 생성

```bash
# master-node

argocd app create my-app \
  --repo https://github.com/jungsukoh/k8s-homelab \
  --path k8s \
  --dest-server https://kubernetes.default.svc \
  --dest-namespace my-app \
  --sync-policy automated \
  --auto-prune \
  --self-heal

# 상태 확인
argocd app get my-app
kubectl get all -n my-app

# 앱 동작 확인
curl http://my-app.local
# Hello from v1!
```

> 브라우저: `https://<ARGOCD_IP>` (인증서 경고 무시)
> my-app 카드 → 파드/서비스/인그레스 그래프 확인

**Auto Sync 확인:**
```bash
# 로컬 PC: k8s/deployment.yaml replicas 2 → 3 으로 수정 후 push

# master-node: 3분 내 자동 반영 확인
kubectl get pods -n my-app
```

---

## 1-4. Rolling Update

```bash
# 로컬 PC: k8s/deployment.yaml 이미지 태그 v1 → v2 로 수정 후 push
# (실제로는 Jenkins가 자동으로 처리하지만, ArgoCD 단독 테스트 시 수동으로)

# master-node: 롤링 진행 실시간 모니터링
kubectl rollout status deployment/my-app -n my-app
watch kubectl get pods -n my-app

# 결과 확인
curl http://my-app.local
# Hello from v2!

# 롤백
kubectl rollout undo deployment/my-app -n my-app
curl http://my-app.local
# Hello from v1!
```

---

## 1-5. Blue/Green 배포

> 로컬 PC: 레포에 아래 파일 추가 후 push

**k8s/deployment-blue.yaml**
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-app-blue
  namespace: my-app
spec:
  replicas: 2
  selector:
    matchLabels:
      app: my-app
      version: blue
  template:
    metadata:
      labels:
        app: my-app
        version: blue
    spec:
      containers:
      - name: my-app
        image: 10.0.0.210/library/my-app:v1
        ports:
        - containerPort: 5000
```

**k8s/deployment-green.yaml**
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-app-green
  namespace: my-app
spec:
  replicas: 2
  selector:
    matchLabels:
      app: my-app
      version: green
  template:
    metadata:
      labels:
        app: my-app
        version: green
    spec:
      containers:
      - name: my-app
        image: 10.0.0.210/library/my-app:v2
        ports:
        - containerPort: 5000
```

**k8s/service.yaml 수정** (selector에 version 추가):
```yaml
spec:
  selector:
    app: my-app
    version: blue     # ← green 으로 수정하면 트래픽 전환
```

```bash
# master-node: Blue/Green 파드 확인
kubectl get pods -n my-app --show-labels

# Blue 응답 확인
curl http://my-app.local
# Hello from v1!

# 로컬 PC: k8s/service.yaml version: green 으로 수정 후 push

# master-node: Green 응답 확인
curl http://my-app.local
# Hello from v2!

# 문제 발생 시: version: blue 로 되돌려서 push → 즉시 롤백
```

---

## 1-6. Canary 배포

> 로컬 PC: 레포에 아래 파일 추가 후 push

**k8s/deployment-stable.yaml**
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-app-stable
  namespace: my-app
spec:
  replicas: 4       # 80% 트래픽
  selector:
    matchLabels:
      app: my-app
  template:
    metadata:
      labels:
        app: my-app
    spec:
      containers:
      - name: my-app
        image: 10.0.0.210/library/my-app:v1
        ports:
        - containerPort: 5000
```

**k8s/deployment-canary.yaml**
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-app-canary
  namespace: my-app
spec:
  replicas: 1       # 20% 트래픽
  selector:
    matchLabels:
      app: my-app
  template:
    metadata:
      labels:
        app: my-app
        track: canary
    spec:
      containers:
      - name: my-app
        image: 10.0.0.210/library/my-app:v2
        ports:
        - containerPort: 5000
```

```bash
# master-node
kubectl get pods -n my-app --show-labels

# 트래픽 분산 확인 (10회 중 약 2회 v2 응답)
for i in {1..10}; do curl -s http://my-app.local; done

# canary 이상 없으면 stable도 v2로 교체 후 push
# 문제 있으면 즉시 차단
kubectl scale deployment my-app-canary -n my-app --replicas=0
```

---

# ━━━ CHAPTER 2: Jenkins CI/CD 실습 ━━━

> 브라우저: `http://<JENKINS_IP>:8080`
> JENKINS_IP 확인:
> ```bash
> # master-node
> kubectl get svc -n jenkins
> ```

## 2-1. Jenkins 플러그인 설치

```
Manage Jenkins → Plugins → Available plugins
```

아래 플러그인 검색 후 체크 → **Install**:
- `Docker Pipeline`
- `Docker Commons Plugin`
- `GitHub Integration Plugin`
- `Git Parameter`
- `Slack Notification`

설치 완료 후:
```
Manage Jenkins → Restart Jenkins when installation is complete 체크
```

## 2-2. Credentials 등록

```
Manage Jenkins → Credentials → System
→ Global credentials (unrestricted) → Add Credentials
```

**① Harbor 접속 정보**
```
Kind     : Username with password
Username : admin
Password : Harbor12345
ID       : harbor-creds
```

**② GitHub PAT**
```
Kind   : Secret text
Secret : (0-3에서 발급한 PAT 붙여넣기)
ID     : github-token
```

**③ Kubeconfig**
```bash
# master-node 에서 kubeconfig 내용 확인
cat ~/.kube/config
# 내용 전체 복사 → 로컬 PC에 config.txt 파일로 저장
```
```
Kind : Secret file
File : config.txt 업로드
ID   : kubeconfig
```

## 2-3. Slack 설정

**Slack Bot Token 발급:**
1. https://api.slack.com/apps → **Create New App → From scratch**
2. App Name: `k8s-jenkins` / 워크스페이스 선택 → Create
3. **OAuth & Permissions → Bot Token Scopes** → `chat:write` 추가
4. **Install to Workspace** → Bot User OAuth Token 복사 (`xoxb-...`)

**Jenkins Slack 설정:**
```
Manage Jenkins → System → Slack 섹션
  Workspace        : (워크스페이스 이름, .slack.com 앞부분)
  Credential       : Add → Secret text → xoxb-... 입력 → ID: slack-bot-token
  Default channel  : #k8s-alerts
→ Test Connection → 성공 확인 → Save
```

## 2-4. Jenkinsfile 작성

> 로컬 PC: `my-k8s-app/Jenkinsfile` 수정 (placeholder 전체 교체)

```groovy
pipeline {
    agent any

    environment {
        HARBOR_URL = '10.0.0.210'
        IMAGE_NAME = 'library/my-app'
        IMAGE_TAG  = "v${BUILD_NUMBER}"
        GIT_REPO   = 'https://github.com/<YOUR_ID>/my-k8s-app'   // ← 변경
        GIT_ID     = '<YOUR_ID>'                                   // ← 변경
    }

    stages {
        // ① 소스 체크아웃
        stage('Checkout') {
            steps {
                git branch: 'main', url: "${GIT_REPO}"
            }
        }

        // ② Docker 이미지 빌드
        stage('Build Image') {
            steps {
                sh """
                    docker build \\
                      -t ${HARBOR_URL}/${IMAGE_NAME}:${IMAGE_TAG} \\
                      -t ${HARBOR_URL}/${IMAGE_NAME}:latest \\
                      .
                """
            }
        }

        // ③ Harbor Push
        stage('Push to Harbor') {
            steps {
                withCredentials([usernamePassword(
                    credentialsId: 'harbor-creds',
                    usernameVariable: 'HARBOR_USER',
                    passwordVariable: 'HARBOR_PASS'
                )]) {
                    sh """
                        docker login ${HARBOR_URL} -u ${HARBOR_USER} -p ${HARBOR_PASS}
                        docker push ${HARBOR_URL}/${IMAGE_NAME}:${IMAGE_TAG}
                        docker push ${HARBOR_URL}/${IMAGE_NAME}:latest
                        docker logout ${HARBOR_URL}
                    """
                }
            }
        }

        // ④ k8s manifest 이미지 태그 업데이트 → Git push
        //    ArgoCD가 이 변경을 감지해서 자동 배포
        stage('Update Manifest') {
            steps {
                withCredentials([string(
                    credentialsId: 'github-token',
                    variable: 'GIT_TOKEN'
                )]) {
                    sh """
                        sed -i 's|image: ${HARBOR_URL}/${IMAGE_NAME}:.*|image: ${HARBOR_URL}/${IMAGE_NAME}:${IMAGE_TAG}|g' \\
                          k8s/deployment.yaml

                        git config user.email "jenkins@local"
                        git config user.name "Jenkins"
                        git add k8s/deployment.yaml
                        git commit -m "ci: update image to ${IMAGE_TAG} [skip ci]"
                        git push https://${GIT_TOKEN}@github.com/${GIT_ID}/my-k8s-app main
                    """
                }
            }
        }

        // ⑤ 배포 완료 대기 (ArgoCD sync 후 rollout 완료까지)
        stage('Verify Deployment') {
            steps {
                withCredentials([file(
                    credentialsId: 'kubeconfig',
                    variable: 'KUBECONFIG'
                )]) {
                    sh """
                        kubectl rollout status deployment/my-app \\
                          -n my-app \\
                          --timeout=180s
                    """
                }
            }
        }
    }

    post {
        success {
            slackSend(
                channel: '#k8s-alerts',
                color: 'good',
                message: """✅ *배포 성공*
Job: ${env.JOB_NAME} | Build: #${env.BUILD_NUMBER}
Image: ${env.IMAGE_TAG} | Duration: ${currentBuild.durationString}"""
            )
        }
        failure {
            slackSend(
                channel: '#k8s-alerts',
                color: 'danger',
                message: """❌ *배포 실패*
Job: ${env.JOB_NAME} | Build: #${env.BUILD_NUMBER}
Log: ${env.BUILD_URL}console"""
            )
        }
    }
}
```

```bash
# 로컬 PC: Jenkinsfile push
git add Jenkinsfile
git commit -m "ci: add Jenkinsfile"
git push origin main
```

## 2-5. Jenkins 파이프라인 생성

```
Jenkins 브라우저 → New Item
  이름 : my-app-pipeline
  종류 : Pipeline
  → OK

Configure:
  Pipeline 섹션
    Definition      : Pipeline script from SCM
    SCM             : Git
    Repository URL  : https://github.com/<YOUR_ID>/my-k8s-app
    Credentials     : github-token
    Branch          : */main
    Script Path     : Jenkinsfile
→ Save → Build Now
```

빌드 로그 확인:
```
my-app-pipeline → #1 → Console Output
① Checkout → ② Build Image → ③ Push → ④ Update Manifest → ⑤ Verify 순서 확인
```

## 2-6. GitHub Webhook 연동

push 시 Jenkins 자동 트리거.

**Jenkins:**
```
my-app-pipeline → Configure → Build Triggers
→ ✅ GitHub hook trigger for GITScm polling → Save
```

**GitHub:**
```
레포 → Settings → Webhooks → Add webhook
  Payload URL  : http://<JENKINS_IP>:8080/github-webhook/
  Content type : application/json
  Events       : Just the push event
→ Add webhook
```

> ⚠️ Jenkins가 외부 인터넷에서 접근 불가한 내부 IP면 Webhook 대신 Poll SCM 사용:
> ```
> Build Triggers → Poll SCM → H/2 * * * *   (2분마다 폴링)
> ```

---

# ━━━ CHAPTER 3: Prometheus + Alertmanager 실습 ━━━

> 모든 명령: **master-node**

## 3-1. 현재 상태 확인

```bash
# master-node
kubectl get pods -n monitoring
kubectl get svc -n monitoring
# 각 서비스 EXTERNAL-IP 확인
```

**브라우저:**
- Grafana: `http://<GRAFANA_IP>` (admin / admin123)
- Prometheus: `http://<PROMETHEUS_IP>:9090`
- Alertmanager: `http://<ALERTMANAGER_IP>:9093`

## 3-2. Slack Incoming Webhook 생성 (Alertmanager용)

Jenkins Slack과 별개로 Alertmanager 전용 Webhook 생성.

1. https://api.slack.com/apps → 앱 선택 (또는 새로 생성)
2. **Incoming Webhooks → Activate: On**
3. **Add New Webhook to Workspace**
4. Slack에서 `#k8s-alerts`, `#k8s-critical` 채널 먼저 생성 후 각각 추가
5. Webhook URL 복사: `https://hooks.slack.com/services/T.../B.../xxx`

## 3-3. Gmail 앱 비밀번호 발급

1. Google 계정 → **보안 → 2단계 인증** 활성화 (필수)
2. **앱 비밀번호** → 앱: 메일 / 기기: 기타 → 생성
3. 16자리 복사: `xxxx xxxx xxxx xxxx`

## 3-4. Alertmanager + Prometheus 설정 적용

```bash
# master-node
cat > ~/monitoring-values.yaml << 'EOF'
alertmanager:
  config:
    global:
      resolve_timeout: 5m
      slack_api_url: 'https://hooks.slack.com/services/T.../B.../xxx'  # ← 3-2 URL
      smtp_smarthost: 'smtp.gmail.com:587'
      smtp_from: 'your-email@gmail.com'             # ← 본인 Gmail
      smtp_auth_username: 'your-email@gmail.com'    # ← 본인 Gmail
      smtp_auth_password: 'xxxx xxxx xxxx xxxx'     # ← 3-3 앱 비밀번호

    route:
      group_by: ['alertname', 'namespace']
      group_wait: 30s
      group_interval: 5m
      repeat_interval: 1h
      receiver: 'slack-default'
      routes:
      - match:
          severity: critical
        receiver: 'slack-critical'
        continue: true
      - match:
          severity: critical
        receiver: 'email-critical'

    receivers:
    - name: 'slack-default'
      slack_configs:
      - channel: '#k8s-alerts'
        send_resolved: true
        color: '{{ if eq .Status "firing" }}warning{{ else }}good{{ end }}'
        title: '[{{ .Status | toUpper }}] {{ .CommonLabels.alertname }}'
        text: |
          *Severity:* {{ .CommonLabels.severity }}
          *Namespace:* {{ .CommonLabels.namespace }}
          {{ range .Alerts }}
          *Pod:* {{ .Labels.pod }}
          *Message:* {{ .Annotations.summary }}
          {{ end }}

    - name: 'slack-critical'
      slack_configs:
      - channel: '#k8s-critical'
        send_resolved: true
        color: 'danger'
        title: '🚨 CRITICAL: {{ .CommonLabels.alertname }}'
        text: |
          {{ range .Alerts }}
          *Summary:* {{ .Annotations.summary }}
          *Description:* {{ .Annotations.description }}
          {{ end }}

    - name: 'email-critical'
      email_configs:
      - to: 'your-email@gmail.com'      # ← 수신 이메일
        send_resolved: true
        subject: '[K8s CRITICAL] {{ .CommonLabels.alertname }}'
        body: |
          Alert: {{ .CommonLabels.alertname }}
          Severity: {{ .CommonLabels.severity }}
          {{ range .Alerts }}
          Summary: {{ .Annotations.summary }}
          Namespace: {{ .Labels.namespace }}
          Pod: {{ .Labels.pod }}
          {{ end }}

grafana:
  service:
    type: LoadBalancer
  adminPassword: admin123

prometheus:
  prometheusSpec:
    serviceMonitorSelectorNilUsesHelmValues: false
    podMonitorSelectorNilUsesHelmValues: false
EOF

# 적용
helm upgrade kube-prometheus-stack \
  prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  -f ~/monitoring-values.yaml

# Alertmanager 재시작 확인
kubectl rollout status statefulset/alertmanager-kube-prometheus-stack-alertmanager \
  -n monitoring
kubectl get pods -n monitoring | grep alertmanager
```

## 3-5. ServiceMonitor 생성 (my-app 메트릭 수집)

```bash
# master-node
cat <<'EOF' | kubectl apply -f -
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: my-app-monitor
  namespace: monitoring
  labels:
    release: kube-prometheus-stack    # 이 라벨 없으면 Prometheus가 인식 안 함
spec:
  namespaceSelector:
    matchNames:
    - my-app
  selector:
    matchLabels:
      app: my-app
  endpoints:
  - port: http          # k8s/service.yaml ports[].name 과 일치해야 함
    path: /metrics
    interval: 15s
EOF

# 등록 확인
kubectl get servicemonitor -n monitoring

# Prometheus UI → Status → Targets → my-app UP 확인
# http://<PROMETHEUS_IP>:9090 → Status → Targets
```

## 3-6. 커스텀 Alert Rule 추가

```bash
# master-node
cat <<'EOF' | kubectl apply -f -
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: my-app-rules
  namespace: monitoring
  labels:
    release: kube-prometheus-stack    # 이 라벨 없으면 Prometheus가 인식 안 함
spec:
  groups:
  - name: my-app.rules
    interval: 30s
    rules:

    - alert: PodCrashLooping
      expr: rate(kube_pod_container_status_restarts_total[5m]) * 300 > 1
      for: 5m
      labels:
        severity: critical
      annotations:
        summary: "파드 재시작 반복: {{ $labels.pod }}"
        description: "{{ $labels.namespace }}/{{ $labels.pod }} 재시작 반복 중"

    - alert: DeploymentUnavailable
      expr: kube_deployment_status_replicas_available == 0
      for: 1m
      labels:
        severity: critical
      annotations:
        summary: "Deployment 파드 없음: {{ $labels.deployment }}"
        description: "{{ $labels.namespace }}/{{ $labels.deployment }} 가용 파드 0개"

    - alert: NodeHighCPU
      expr: 100 - (avg by(instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100) > 80
      for: 2m
      labels:
        severity: warning
      annotations:
        summary: "노드 CPU 높음: {{ $labels.instance }}"
        description: "CPU 사용률 {{ $value | printf \"%.1f\" }}%"

    - alert: NodeHighMemory
      expr: (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100 > 85
      for: 2m
      labels:
        severity: warning
      annotations:
        summary: "노드 메모리 높음: {{ $labels.instance }}"
        description: "메모리 사용률 {{ $value | printf \"%.1f\" }}%"

    - alert: PodOOMKilled
      expr: kube_pod_container_status_last_terminated_reason{reason="OOMKilled"} == 1
      for: 0m
      labels:
        severity: critical
      annotations:
        summary: "파드 OOM: {{ $labels.pod }}"
        description: "{{ $labels.namespace }}/{{ $labels.pod }} OOMKilled 발생"

    - alert: PVCHighUsage
      expr: (kubelet_volume_stats_used_bytes / kubelet_volume_stats_capacity_bytes) * 100 > 80
      for: 5m
      labels:
        severity: warning
      annotations:
        summary: "PVC 사용량 높음: {{ $labels.persistentvolumeclaim }}"
        description: "{{ $labels.namespace }}/{{ $labels.persistentvolumeclaim }} 사용률 {{ $value | printf \"%.1f\" }}%"
EOF

# 확인
kubectl get prometheusrule -n monitoring
# Prometheus UI → Alerts 탭에서 my-app-rules 확인
```

## 3-7. Alert 강제 발생 테스트

**① CrashLoopBackOff 파드 생성 (5분 후 Slack 알림 수신 확인)**

```bash
# master-node
cat <<'EOF' | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: crash-test
  namespace: my-app
spec:
  containers:
  - name: crash
    image: busybox
    command: ["sh", "-c", "echo crashing; exit 1"]
  restartPolicy: Always
EOF

# 재시작 반복 확인
kubectl get pods crash-test -n my-app -w
# STATUS: CrashLoopBackOff 확인 → 약 5분 후 Slack #k8s-alerts 알림 확인

# 테스트 완료 후 삭제
kubectl delete pod crash-test -n my-app
```

**② Alertmanager API로 즉시 알림 테스트**

```bash
# master-node
kubectl port-forward svc/kube-prometheus-stack-alertmanager \
  -n monitoring 9093:9093 &

curl -X POST http://localhost:9093/api/v1/alerts \
  -H 'Content-Type: application/json' \
  -d '[{
    "labels": {
      "alertname": "TestAlert",
      "severity": "critical",
      "namespace": "my-app"
    },
    "annotations": {
      "summary": "Alertmanager 연동 테스트",
      "description": "Slack + 이메일 알림 테스트"
    }
  }]'

# Slack #k8s-critical 채널 + 이메일 즉시 수신 확인
kill %1   # port-forward 종료
```

## 3-8. Grafana 대시보드

```
Grafana → Dashboards → Import → ID 입력 → Load → Import
```

| 용도 | ID |
|------|----|
| K8s 클러스터 전체 | 7249 |
| Node Exporter 상세 | 1860 |
| K8s 파드 모니터링 | 6781 |
| ArgoCD | 14584 |
| Jenkins 성능 | 9964 |
| K8s Deployment | 8588 |

**my-app 커스텀 패널:**
```
대시보드 → Add visualization
  Data source : Prometheus
  Query       : rate(http_requests_total{job="my-app"}[5m])
  Title       : my-app RPS
  → Apply → Save dashboard
```

---

# ━━━ STEP 4: End-to-End 전체 흐름 검증 ━━━

```bash
# 1. 로컬 PC: app/main.py 메시지 수정 후 push
#    return f"Hello from {VERSION} - updated!\n"
git add .
git commit -m "feat: update response message"
git push origin main
```

```
# 2. Jenkins 빌드 자동 시작 확인
#    http://<JENKINS_IP>:8080 → my-app-pipeline → Console Output
#    ① Checkout ② Build Image ③ Push ④ Update Manifest ⑤ Verify
```

```bash
# 3. ArgoCD sync 확인
#    https://<ARGOCD_IP> → my-app → Syncing → Synced

# master-node
argocd app get my-app
kubectl rollout status deployment/my-app -n my-app
```

```bash
# 4. 배포 결과 확인
# master-node
curl http://my-app.local
# Hello from v<N> - updated!
```

```bash
# 5. Prometheus 메트릭 + 부하 발생
# master-node
for i in {1..50}; do curl -s http://my-app.local > /dev/null; done
# Prometheus UI → Query: rate(http_requests_total{job="my-app"}[5m])
```

```
# 6. Grafana 확인
#    my-app RPS 패널에 트래픽 그래프 표시 확인
```

```bash
# 7. Slack 알림 확인
#    Jenkins 빌드 완료 시 → #k8s-alerts 에 ✅ 배포 성공 메시지 확인
#    CrashLoop 파드 생성 → 5분 후 #k8s-alerts 알림 확인
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: crash-final-test
  namespace: my-app
spec:
  containers:
  - name: crash
    image: busybox
    command: ["sh", "-c", "exit 1"]
  restartPolicy: Always
EOF

# 알림 수신 후 삭제
kubectl delete pod crash-final-test -n my-app
```
