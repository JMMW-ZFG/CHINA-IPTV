import requests
import concurrent.futures
import re
import os

# ================= 新增：无效链接检测模块 =================
def check_channel(name, url, timeout=3):
    """检测单个频道链接是否有效，返回 (名称, URL, 是否有效)"""
    try:
        # 使用 HEAD 请求，只获取响应头不下载视频，速度极快
        response = requests.head(url.strip(), timeout=timeout, allow_redirects=True)
        return (name, url, response.status_code in [200, 302])
    except requests.exceptions.RequestException:
        return (name, url, False)

def filter_invalid_channels(channels, max_workers=30):
    """并发检测所有频道链接，剔除死链
    channels: list of (name, url)
    返回: list of (name, url) 仅保留有效链接
    """
    print(f"🚀 正在检测 {len(channels)} 个频道的链接有效性...")
    valid = []
    invalid = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_ch = {executor.submit(check_channel, name, url): (name, url) for name, url in channels}
        for future in concurrent.futures.as_completed(future_to_ch):
            name, url, is_valid = future.result()
            if is_valid:
                valid.append((name, url))
            else:
                invalid += 1
    print(f"✅ 检测完成！共剔除 {invalid} 个死链，保留 {len(valid)} 个有效频道。")
    return valid


# ================= 原有核心逻辑 =================
def normalize_channel_name(name):
    if not name:
        return ''
    # 去除电视台、空格、括号等，并移除末尾常见后缀
    normalized = re.sub(r'[()（）\s_\-‑]+|电视台', '', name)
    normalized = re.sub(r'(频道|普清|标清|高清|超清|超高清|720P|1080P|HD)$', '', normalized, flags=re.IGNORECASE)
    normalized = re.sub(r'[-\s_]+$', '', normalized)
    return normalized.upper().strip()

def load_source_urls():
    source_path = "TV/sources.txt"
    urls = []
    if not os.path.exists(source_path):
        print(f"警告：未找到源地址文件 {source_path}，使用默认源")
        return ["https://live.fanmingming.com/tv/m3u/ipv6.m3u"]
    try:
        with open(source_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if line.startswith('http'):
                    urls.append(line)
                    print(f"加载源地址: {line}")
    except Exception as e:
        print(f"读取源地址文件失败: {e}")
        return ["https://live.fanmingming.com/tv/m3u/ipv6.m3u"]
    if not urls:
        print("警告：源地址文件为空，使用默认源")
        return ["https://live.fanmingming.com/tv/m3u/ipv6.m3u"]
    return urls

def load_categories_from_template():
    categories = {}
    current_category = None
    template_path = "TV/moban.txt"
    if not os.path.exists(template_path):
        print(f"错误：未找到模板文件 {template_path}")
        return categories
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if ",#genre#" in line:
                    current_category = line.split(",")[0].strip()
                    categories[current_category] = []
                elif current_category:
                    channel = line.strip()
                    if channel:
                        categories[current_category].append(channel)
    except Exception as e:
        print(f"读取模板文件出错: {e}")
    return categories

def load_channel_mapping():
    mapping = {}
    mapping_path = "TV/channel_mapping.txt"
    if not os.path.exists(mapping_path):
        return mapping
    try:
        with open(mapping_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or "," not in line:
                    continue
                old_name, new_name = line.split(",", 1)
                std_key = normalize_channel_name(old_name.strip())
                std_value = normalize_channel_name(new_name.strip())
                mapping[std_key] = std_value
        print(f"加载映射表成功，共 {len(mapping)} 条映射")
    except Exception as e:
        print(f"加载映射表失败: {e}")
    return mapping

def parse_content(content):
    """解析内容，提取所有 (名称, URL) 列表，忽略分组信息。支持 TXT 和 M3U 格式。"""
    lines = content.split('\n')
    channels = []
    is_m3u = '#EXTM3U' in content or '#EXTINF' in content

    if is_m3u:
        for i in range(len(lines)):
            line = lines[i].strip()
            if line.startswith('#EXTINF:'):
                name_match = re.search(r'tvg-name="([^"]*)"', line)
                name = name_match.group(1) if name_match else line.split(',')[-1].strip()
                name = normalize_channel_name(name)
                if i + 1 < len(lines):
                    url = lines[i + 1].strip()
                    if url and not url.startswith('#'):
                        channels.append((name, url))
    else:
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if ', #genre#' in line or ',#genre#' in line:
                continue
            if ',' in line:
                parts = line.split(',', 1)
                if len(parts) == 2:
                    name = normalize_channel_name(parts[0].strip())
                    url = parts[1].strip()
                    if url and not url.startswith('#'):
                        channels.append((name, url))
    return channels

def fetch_content(url):
    try:
        print(f"正在获取: {url}")
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(url, timeout=15, headers=headers)
        response.raise_for_status()
        content = response.text
        return parse_content(content)
    except Exception as e:
        print(f"请求失败: {e}")
        return []

def main():
    if not os.path.exists("TV"):
        os.makedirs("TV")

    source_urls = load_source_urls()
    print(f"\n共加载 {len(source_urls)} 个源地址")

    # 收集所有频道的 (标准化名称, URL) ，并全局去重（URL去重）
    all_channels = []
    seen_urls = set()
    total_raw = 0

    for idx, url in enumerate(source_urls, 1):
        print(f"\n--- 处理第 {idx}/{len(source_urls)} 个源 ---")
        channels = fetch_content(url)
        if not channels:
            print(f"❌ 源 {idx} 获取失败或内容为空")
            continue
        print(f"✅ 源 {idx} 获取成功，原始频道数: {len(channels)}")
        for name, channel_url in channels:
            total_raw += 1
            if channel_url not in seen_urls:
                seen_urls.add(channel_url)
                all_channels.append((name, channel_url))
    print(f"\n📊 共获取 {len(all_channels)} 个去重后的频道")

    # 【新增】在分类之前，先过滤掉无效链接
    all_channels = filter_invalid_channels(all_channels)
    if not all_channels:
        print("错误：所有链接均无效，请检查网络或源地址")
        return

    if not all_channels:
        print("错误：未能获取任何有效内容")
        return

    # 加载模板和映射表
    categories = load_categories_from_template()
    if not categories:
        print("分类数据为空，请检查模板文件格式")
        return

    mapping = load_channel_mapping()

    # 对频道名称应用映射表（标准化名称 -> 映射后名称）
    mapped_channels = []
    for name, url in all_channels:
        mapped_name = mapping.get(name, name)
        mapped_channels.append((mapped_name, url))

    # 构建模板分类的映射：模板中的每个标准化名称 -> 所属分类
    template_channel_to_category = {}
    for cat, ch_list in categories.items():
        for ch in ch_list:
            template_channel_to_category[normalize_channel_name(ch)] = cat

    # 分类存放结果
    category_dict = {cat: [] for cat in categories}
    other_list = []

    # 对每个频道，根据映射后的名称归类
    for name, url in mapped_channels:
        if name in template_channel_to_category:
            cat = template_channel_to_category[name]
            category_dict[cat].append((name, url))
        else:
            other_list.append((name, url))

    # 按模板顺序输出每个分类
    sorted_content = []
    matched_count = 0
    for cat, ch_list in categories.items():
        items = category_dict[cat]
        if items:
            # 按模板顺序排序（构建索引）
            index_map = {normalize_channel_name(ch): idx for idx, ch in enumerate(ch_list)}
            items.sort(key=lambda item: index_map.get(item[0], len(ch_list) + 1))
            sorted_content.append(f"{cat},#genre#")
            for name, url in items:
                sorted_content.append(f"{name},{url}")
            sorted_content.append("")
            matched_count += len(items)

    # 输出“其它”
    if other_list:
        # 对“其它”按名称排序（可选）
        other_list.sort(key=lambda x: x[0])
        sorted_content.append("其它,#genre#")
        for name, url in other_list:
            sorted_content.append(f"{name},{url}")
        sorted_content.append("")

    # 写入文件
    output_path = "TV/live.txt"
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            # 固定声明（无空行，与网页版一致）
            declaration = "【来源】https://github.com/xisohi/CHINA-IPTV"
            f.write(declaration + "\n\n" + "\n".join(sorted_content))
        print(f"\n✅ 多源合并完成，已保存为 {output_path}")
        print(f"📊 统计: {matched_count}个匹配频道, {len(other_list)}个未分类频道")
        print(f"📊 总计写入频道数: {matched_count + len(other_list)}")
    except Exception as e:
        print(f"保存文件时出错: {e}")

if __name__ == "__main__":
    main() 
