import os
import requests
import socket
import csv
import ipaddress
from concurrent.futures import ThreadPoolExecutor, as_completed
import geoip2.database

# 配置参数
API_URL = "https://api.edgeone.ai/ips?version=v4&area=overseas"
TIMEOUT = 2    # 检测超时时间（秒）
THREADS = 100  # 增加并发线程数（根据网络调整）
PORT = 443     # 检测端口（HTTPS）
GEOIP_DB_FILE = 'Country.mmdb'  # 合并国家+ASN的数据库

def get_cidr_list():
    """获取CIDR列表（带重试机制）"""
    api_endpoints = [
        "https://api.edgeone.ai/ips?version=v4&area=overseas",
        "https://cdn-l3.edgio.net/ips?version=v4&area=overseas"
    ]
    
    for url in api_endpoints:
        for attempt in range(3):
            try:
                resp = requests.get(url, timeout=5)
                if resp.status_code == 200:
                    return [line.strip() for line in resp.text.splitlines() if line.strip()]
            except Exception as e:
                print(f"获取CIDR失败 (第{attempt+1}次尝试): {str(e)}")
                if attempt < 2:
                    import time; time.sleep(1)
    return []

def cidr_to_ips(cidr):
    """将CIDR转换为所有可用IP列表"""
    try:
        network = ipaddress.ip_network(cidr, strict=False)
        return [str(ip) for ip in network.hosts()]
    except:
        print(f"无效的CIDR格式: {cidr}")
        return []

def check_ip_alive(ip):
    """TCP端口存活检测（带连接快速失败）"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(TIMEOUT)
            s.connect_ex((ip, PORT))  # 使用非阻塞模式
            return ip, True
    except:
        return ip, False

def get_ip_info(ip, geo_reader):
    """获取IP详细信息（带错误重试）"""
    for retry in range(2):  # 最多重试2次
        try:
            response = geo_reader.country(ip)
            return {
                'ip': ip,
                'country': response.country.name or "Unknown",
                'asn': response.traits.autonomous_system_number or "Unknown",
                'organization': response.traits.autonomous_system_organization or "Unknown",
                'error': None
            }
        except Exception as e:
            if retry == 1:  # 最后一次尝试仍然失败
                return {'ip': ip, 'error': str(e)}
            continue

def main():
    # 下载GeoIP数据库（如果不存在）
    if not os.path.exists(GEOIP_DB_FILE):
        print("正在下载综合GeoIP数据库...")
        try:
            url = "https://github.com/Loyalsoldier/geoip/releases/download/202505010022/Country.mmdb"
            with requests.get(url, stream=True) as r:
                r.raise_for_status()
                with open(GEOIP_DB_FILE, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
        except Exception as e:
            print(f"数据库下载失败: {str(e)}")
            return

    geo_reader = geoip2.database.Reader(GEOIP_DB_FILE)

    # 获取并处理CIDR列表
    print("正在获取CIDR列表...")
    cidr_list = get_cidr_list()
    if not cidr_list:
        print("无法获取CIDR列表，请检查网络连接")
        return

    # 生成全量IP列表
    all_ips = []
    total_ips = 0
    for cidr in cidr_list:
        ips = cidr_to_ips(cidr)
        total_ips += len(ips)
        all_ips.extend(ips)
        print(f"CIDR {cidr} 生成 {len(ips)} 个IP")

    print(f"即将开始检测 {total_ips} 个IP地址（请耐心等待）...")

    # 存活检测（带进度显示）
    alive_ips = []
    with ThreadPoolExecutor(max_workers=THREADS) as executor:
        futures = {executor.submit(check_ip_alive, ip): ip for ip in all_ips}
        for i, future in enumerate(as_completed(futures)):
            ip, status = future.result()
            if status:
                alive_ips.append(ip)
                print(f"✅ 存活 ({len(alive_ips)}/{i+1}) {ip}")
            else:
                print(f"❌ 失效 ({i+1}/{total_ips}) {ip}")

    # 信息查询（带速率限制）
    results = []
    with ThreadPoolExecutor(max_workers=THREADS//2) as executor:  # 降低查询并发
        futures = {executor.submit(get_ip_info, ip, geo_reader): ip for ip in alive_ips}
        for future in as_completed(futures):
            data = future.result()
            if not data.get('error'):
                results.append([
                    data['ip'],
                    data['country'],
                    data['asn'],
                    data['organization']
                ])
                print(f"已处理 {len(results)}/{len(alive_ips)} IP")
            else:
                results.append([data['ip'], "Error", data['error'], ""])
                print(f"查询失败: {data['ip']} - {data['error']}")

    # 保存完整结果
    with open('full_cdn_nodes.csv', 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['IP', 'Country', 'ASN', 'ISP'])
        writer.writerows(results)
    print(f"完整结果已保存到 full_cdn_nodes.csv")

if __name__ == "__main__":
    main()