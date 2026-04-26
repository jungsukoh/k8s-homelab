pipeline {
    agent any

    environment {
        HARBOR_URL = '10.0.0.208'
        IMAGE_NAME = 'library/my-app'
        IMAGE_TAG  = "v${BUILD_NUMBER}"
    }

    stages {
        stage('Checkout') {
            steps {
                git branch: 'main',
                    url: 'https://github.com/jungsukoh/k8s-homelab'
            }
        }

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
                        git push https://${GIT_TOKEN}@github.com/jungsukoh/k8s-homelab main
                    """
                }
            }
        }

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
                message: """? *배포 성공*
Job: ${env.JOB_NAME} | Build: #${env.BUILD_NUMBER}
Image: ${env.IMAGE_TAG} | Duration: ${currentBuild.durationString}"""
            )
        }
        failure {
            slackSend(
                channel: '#k8s-alerts',
                color: 'danger',
                message: """? *배포 실패*
Job: ${env.JOB_NAME} | Build: #${env.BUILD_NUMBER}
Log: ${env.BUILD_URL}console"""
            )
        }
    }
}