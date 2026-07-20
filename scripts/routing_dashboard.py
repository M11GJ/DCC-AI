#!/usr/bin/env python3
"""
DCC AI - Routing Dashboard Server
LiteLLM の Prometheus メトリクスを取得し、美しく可視化するダッシュボード。
Python 標準ライブラリのみで構成（追加パッケージ不要）。
"""
import http.server
import json
import re
import socketserver
import urllib.request
import urllib.error
import sys

import os

PORT = int(os.environ.get("PORT", "3001"))
LITELLM_METRICS_URL = os.environ.get("LITELLM_METRICS_URL", "http://127.0.0.1:4000/metrics/")

def load_all_models():
    models = []
    try:
        with open("/app/config.yaml", "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return models

    ids = re.findall(r'id:\s*["\']?([^"\',\}]+)["\']?', content)
    for model_id in ids:
        name = model_id.strip()
        if name and name not in models:
            models.append(name)
    return models

def fetch_and_parse_metrics():
    try:
        req = urllib.request.Request(LITELLM_METRICS_URL)
        with urllib.request.urlopen(req, timeout=3) as r:
            content = r.read().decode('utf-8')
    except urllib.error.URLError as e:
        return {"error": f"LiteLLM (localhost:4000) への接続に失敗しました: {e.reason}"}
    except Exception as e:
        return {"error": f"メトリクスの取得中にエラーが発生しました: {e}"}

    stats = {
        "deployments": {},
        "totals": {
            "success": 0,
            "failed": 0,
            "total": 0,
            "success_rate": 100.0
        }
    }

    # config.yaml から設定済みの全モデルIDをプリロードし、リクエスト数0でもUIに表示されるようにする
    for m_id in load_all_models():
        stats["deployments"][m_id] = {
            "model_id": m_id,
            "api_key_name": "Active" if "Gemini Key" in m_id else "Configured",
            "success": 0,
            "failed": 0,
            "latency_sum": 0.0,
            "latency_count": 0,
            "avg_latency": 0.0
        }

    # Regex for Prometheus: metric_name{labels} value
    metric_re = re.compile(r'^(\w+)\{(.*?)\}\s+(.+)$', re.MULTILINE)
    label_re = re.compile(r'(\w+)="([^"]*)"')

    for match in metric_re.finditer(content):
        metric_name, label_str, val_str = match.groups()
        try:
            val = float(val_str.strip())
        except ValueError:
            val = 0.0

        labels = {}
        for l_match in label_re.finditer(label_str):
            k, v = l_match.groups()
            labels[k] = v

        model_id = labels.get("model_id") or labels.get("model") or "unknown"
        api_key_name = labels.get("api_key_name") or labels.get("key") or "unknown"

        # 古いキー指定などの表示をクリーンアップ
        api_key_name = api_key_name.replace("os.environ/", "")

        dep_key = f"{model_id} [{api_key_name}]" if api_key_name != "unknown" else model_id
        if dep_key not in stats["deployments"]:
            stats["deployments"][dep_key] = {
                "model_id": model_id,
                "api_key_name": api_key_name,
                "success": 0,
                "failed": 0,
                "latency_sum": 0.0,
                "latency_count": 0,
                "avg_latency": 0.0
            }

        dep = stats["deployments"][dep_key]

        if metric_name == "litellm_requests_metric_total":
            dep["success"] = int(val)
            stats["totals"]["success"] += int(val)
        elif metric_name == "litellm_deployment_failure_responses_total":
            dep["failed"] = int(val)
            stats["totals"]["failed"] += int(val)
        elif metric_name == "litellm_request_total_latency_metric_sum":
            dep["latency_sum"] = val
        elif metric_name == "litellm_request_total_latency_metric_count":
            dep["latency_count"] = int(val)

    # 成功率計算
    totals = stats["totals"]
    totals["total"] = totals["success"] + totals["failed"]
    if totals["total"] > 0:
        totals["success_rate"] = round((totals["success"] / totals["total"]) * 100, 1)

    # 平均レイテンシ計算
    for dep in stats["deployments"].values():
        if dep["latency_count"] > 0:
            dep["avg_latency"] = round(dep["latency_sum"] / dep["latency_count"], 3)
        else:
            dep["avg_latency"] = 0.0

    return stats

HTML_DASHBOARD = """<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DCC AI - Routing Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=Inter:wght@300;400;600&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {
            --bg-color: #0b0f19;
            --card-bg: #131a2e;
            --border-color: #1f2937;
            --text-color: #f3f4f6;
            --text-muted: #9ca3af;
            --primary: #8b5cf6;
            --secondary: #10b981;
            --error: #ef4444;
            --accent: #06b6d4;
        }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            background-color: var(--bg-color);
            color: var(--text-color);
            font-family: 'Inter', sans-serif;
            min-height: 100vh;
            padding: 2rem;
            display: flex;
            flex-direction: column;
            align-items: center;
        }

        header {
            width: 100%;
            max-width: 1200px;
            margin-bottom: 2rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 1rem;
        }

        h1 {
            font-family: 'Outfit', sans-serif;
            font-size: 2.2rem;
            font-weight: 800;
            background: linear-gradient(135deg, #a78bfa 0%, #3b82f6 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .status-badge {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            background: rgba(16, 185, 129, 0.1);
            border: 1px solid var(--secondary);
            color: var(--secondary);
            padding: 0.5rem 1rem;
            border-radius: 9999px;
            font-size: 0.9rem;
            font-weight: 600;
        }

        .status-badge.error {
            background: rgba(239, 68, 68, 0.1);
            border: 1px solid var(--error);
            color: var(--error);
        }

        .container {
            width: 100%;
            max-width: 1200px;
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 1.5rem;
        }

        .metric-card {
            background-color: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1.5rem;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            transition: transform 0.2s, border-color 0.2s;
        }

        .metric-card:hover {
            transform: translateY(-2px);
            border-color: var(--primary);
        }

        .metric-label {
            color: var(--text-muted);
            font-size: 0.85rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 0.5rem;
        }

        .metric-value {
            font-family: 'Outfit', sans-serif;
            font-size: 2rem;
            font-weight: 800;
        }

        .chart-card {
            grid-column: span 2;
            background-color: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1.5rem;
            min-height: 350px;
            display: flex;
            flex-direction: column;
        }

        .chart-header {
            font-family: 'Outfit', sans-serif;
            font-size: 1.2rem;
            font-weight: 600;
            margin-bottom: 1rem;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 0.5rem;
        }

        .chart-container {
            position: relative;
            flex-grow: 1;
            width: 100%;
        }

        .table-card {
            grid-column: span 4;
            background-color: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1.5rem;
            overflow-x: auto;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            text-align: left;
            margin-top: 1rem;
        }

        th {
            color: var(--text-muted);
            font-size: 0.85rem;
            font-weight: 600;
            text-transform: uppercase;
            border-bottom: 1px solid var(--border-color);
            padding: 0.75rem 1rem;
        }

        td {
            padding: 1rem;
            border-bottom: 1px solid #1f2937;
            font-size: 0.95rem;
        }

        tr:last-child td {
            border-bottom: none;
        }

        .pulse {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background-color: var(--secondary);
            animation: pulse-animation 2s infinite;
        }

        @keyframes pulse-animation {
            0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); }
            70% { transform: scale(1); box-shadow: 0 0 0 8px rgba(16, 185, 129, 0); }
            100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }
        }

        @media (max-width: 1024px) {
            .container { grid-template-columns: repeat(2, 1fr); }
            .chart-card { grid-column: span 2; }
            .table-card { grid-column: span 2; }
        }

        @media (max-width: 640px) {
            body { padding: 1rem; }
            .container { grid-template-columns: 1fr; }
            .chart-card { grid-column: span 1; }
            .table-card { grid-column: span 1; }
        }
    </style>
</head>
<body>
    <header>
        <div>
            <h1>DCC AI Routing Dashboard</h1>
            <p style="color: var(--text-muted); font-size: 0.9rem; margin-top: 0.2rem;">Upstream API Routing & Load Monitor</p>
        </div>
        <div id="status-container" class="status-badge">
            <div class="pulse"></div>
            <span id="status-text">Connected</span>
        </div>
    </header>

    <div class="container">
        <!-- Summary metrics -->
        <div class="metric-card">
            <div class="metric-label">Total Requests</div>
            <div id="total-req" class="metric-value">0</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">Success Rate</div>
            <div id="success-rate" class="metric-value" style="color: var(--secondary);">100%</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">Active Backends</div>
            <div id="active-keys" class="metric-value" style="color: var(--accent);">0</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">Auto Refresh</div>
            <div class="metric-value" style="font-size: 1.5rem; display: flex; align-items: center; gap: 0.5rem;">
                <span>Every 3s</span>
            </div>
        </div>

        <!-- Charts -->
        <div class="chart-card">
            <div class="chart-header">Request Distribution (Key / Deployments)</div>
            <div class="chart-container">
                <canvas id="reqChart"></canvas>
            </div>
        </div>

        <div class="chart-card">
            <div class="chart-header">Average Latency (seconds)</div>
            <div class="chart-container">
                <canvas id="latencyChart"></canvas>
            </div>
        </div>

        <!-- Table -->
        <div class="table-card">
            <div class="chart-header">Routing Details</div>
            <table>
                <thead>
                    <tr>
                        <th>Model ID</th>
                        <th>API Key / Endpoint</th>
                        <th>Success Requests</th>
                        <th>Failed Requests</th>
                        <th>Avg Latency</th>
                    </tr>
                </thead>
                <tbody id="table-body">
                    <tr>
                        <td colspan="5" style="text-align: center; color: var(--text-muted);">No metrics data loaded. Waiting for API traffic...</td>
                    </tr>
                </tbody>
            </table>
        </div>
    </div>

    <script>
        let reqChart = null;
        let latencyChart = null;

        function initCharts(labels, counts, latencies) {
            const ctxReq = document.getElementById('reqChart').getContext('2d');
            const ctxLat = document.getElementById('latencyChart').getContext('2d');

            Chart.defaults.color = '#9ca3af';
            Chart.defaults.font.family = 'Inter';

            reqChart = new Chart(ctxReq, {
                type: 'bar',
                data: {
                    labels: labels,
                    datasets: [{
                        label: 'Successful Requests',
                        data: counts,
                        backgroundColor: 'rgba(139, 92, 246, 0.6)',
                        borderColor: 'rgba(139, 92, 246, 1)',
                        borderWidth: 1,
                        borderRadius: 4
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        y: { beginAtZero: true, grid: { color: '#1f2937' } },
                        x: { grid: { display: false } }
                    },
                    plugins: { legend: { display: false } }
                }
            });

            latencyChart = new Chart(ctxLat, {
                type: 'bar',
                data: {
                    labels: labels,
                    datasets: [{
                        label: 'Avg Latency (s)',
                        data: latencies,
                        backgroundColor: 'rgba(6, 182, 212, 0.6)',
                        borderColor: 'rgba(6, 182, 212, 1)',
                        borderWidth: 1,
                        borderRadius: 4
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        y: { beginAtZero: true, grid: { color: '#1f2937' } },
                        x: { grid: { display: false } }
                    },
                    plugins: { legend: { display: false } }
                }
            });
        }

        function updateCharts(labels, counts, latencies) {
            if (!reqChart) {
                initCharts(labels, counts, latencies);
                return;
            }

            reqChart.data.labels = labels;
            reqChart.data.datasets[0].data = counts;
            reqChart.update();

            latencyChart.data.labels = labels;
            latencyChart.data.datasets[0].data = latencies;
            latencyChart.update();
        }

        async function fetchStats() {
            try {
                const response = await fetch('/api/stats');
                const data = await response.json();

                if (data.error) {
                    showError(data.error);
                    return;
                }

                // Header status
                document.getElementById('status-container').className = 'status-badge';
                document.getElementById('status-text').innerText = 'Connected';

                // Totals
                document.getElementById('total-req').innerText = data.totals.total;
                document.getElementById('success-rate').innerText = data.totals.success_rate + '%';
                if (data.totals.success_rate < 80.0) {
                    document.getElementById('success-rate').style.color = 'var(--error)';
                } else {
                    document.getElementById('success-rate').style.color = 'var(--secondary)';
                }

                const deployments = Object.keys(data.deployments);
                document.getElementById('active-keys').innerText = deployments.length;

                // Prepare charts data
                const labels = [];
                const successCounts = [];
                const latencies = [];
                const tbody = document.getElementById('table-body');
                tbody.innerHTML = '';

                if (deployments.length === 0) {
                    tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--text-muted);">No metrics data registered yet. Run some chat queries!</td></tr>`;
                    updateCharts([], [], []);
                    return;
                }

                deployments.forEach(key => {
                    const d = data.deployments[key];
                    
                    // Label styling
                    const shortLabel = d.api_key_name !== 'unknown' ? `${d.model_id.split('/').pop()} (${d.api_key_name})` : d.model_id.split('/').pop();
                    labels.push(shortLabel);
                    successCounts.push(d.success);
                    latencies.push(d.avg_latency);

                    const row = document.createElement('tr');
                    row.innerHTML = `
                        <td style="font-weight: 600;">${d.model_id}</td>
                        <td style="color: var(--accent); font-family: monospace;">${d.api_key_name}</td>
                        <td style="color: var(--secondary); font-weight: bold;">${d.success}</td>
                        <td style="color: ${d.failed > 0 ? 'var(--error)' : 'var(--text-muted)'};">${d.failed}</td>
                        <td style="font-weight: 600;">${d.avg_latency}s</td>
                    `;
                    tbody.appendChild(row);
                });

                updateCharts(labels, successCounts, latencies);

            } catch (err) {
                showError("サーバーに接続できません");
            }
        }

        function showError(msg) {
            document.getElementById('status-container').className = 'status-badge error';
            document.getElementById('status-text').innerText = 'Offline';
            const tbody = document.getElementById('table-body');
            tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--error);">${msg}</td></tr>`;
        }

        // Loop
        fetchStats();
        setInterval(fetchStats, 3000);
    </script>
</body>
</html>
"""

class DashboardHTTPHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_DASHBOARD.encode('utf-8'))
        elif self.path == '/api/stats':
            stats = fetch_and_parse_metrics()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(stats, ensure_ascii=False).encode('utf-8'))
        else:
            self.send_error(404, "Not Found")

def main():
    print(f"Starting DCC AI Routing Dashboard on port {PORT}...")
    print(f"Targeting LiteLLM metrics at: {LITELLM_METRICS_URL}")
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), DashboardHTTPHandler) as httpd:
        print(f"Dashboard is live: http://localhost:{PORT}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down dashboard...")
            sys.exit(0)

if __name__ == "__main__":
    main()
