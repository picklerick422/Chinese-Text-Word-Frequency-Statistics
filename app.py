import time
import jieba
from flask import Flask, request, render_template, flash, redirect, url_for
from collections import Counter
from docx import Document

# 预加载 jieba 词典
jieba.initialize()

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'  # 请修改为随机字符串
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 限制上传文件 16MB


def count_exact(text, words):
    """精确模式：字符串count，返回结果和总词数（分词后）"""
    seg_list = list(jieba.cut(text))
    total_words = len(seg_list)
    results = {w: text.count(w) for w in words}
    return results, total_words


def count_seg(text, words):
    """分词模式：文本分词后，查找每个短语的分词子序列，返回结果和总词数"""
    seg_list = list(jieba.cut(text))
    total_words = len(seg_list)
    results = {}
    for phrase in words:
        target = list(jieba.cut(phrase))
        results[phrase] = count_subsequence(seg_list, target)
    return results, total_words


def count_subsequence(seq, target):
    """统计target作为连续子序列在seq中出现的次数"""
    if not target or len(target) > len(seq):
        return 0
    t_len = len(target)
    return sum(1 for i in range(len(seq) - t_len + 1) if seq[i:i + t_len] == target)


def search_containing_words(text, keywords):
    """搜索模式：找出所有包含关键词的词及其频次，返回结果和总词数"""
    seg_list = list(jieba.cut(text))
    total_words = len(seg_list)
    word_counts = Counter(seg_list)
    results = {}
    for kw in keywords:
        matches = {word: cnt for word, cnt in word_counts.items() if kw in word}
        # 按频次降序排序
        results[kw] = dict(sorted(matches.items(), key=lambda x: x[1], reverse=True))
    return results, total_words


def parse_categories(categories_raw):
    """解析类别定义字符串，返回字典 {类别名: [词1, 词2, ...]}"""
    categories = {}
    if not categories_raw:
        return categories
    for line in categories_raw.strip().splitlines():
        line = line.strip()
        if not line or ':' not in line:
            continue
        name, words_part = line.split(':', 1)
        name = name.strip()
        words = [w.strip() for w in words_part.split(',') if w.strip()]
        if name and words:
            categories[name] = words
    return categories


@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        input_type = request.form.get('input_type', 'file')  # 默认文件上传

        # 获取文本内容
        text = None
        if input_type == 'file':
            file = request.files.get('file')
            if not file or file.filename == '':
                flash('请选择一个文件')
                return redirect(request.url)
            try:
                filename = file.filename.lower()
                if filename.endswith('.docx'):
                    # 处理 Word 文档（.docx）
                    # 先将上传内容读到内存，再交给 python-docx 解析
                    from io import BytesIO
                    file_bytes = file.read()
                    doc = Document(BytesIO(file_bytes))
                    paragraphs = [p.text for p in doc.paragraphs if p.text]
                    text = '\n'.join(paragraphs)
                else:
                    # 默认按 UTF-8 文本文件处理（如 .txt）
                    text = file.read().decode('utf-8')
            except Exception as e:
                flash(f'文件读取失败：{e}')
                return redirect(request.url)
        else:  # 直接输入文本
            text = request.form.get('direct_text', '').strip()
            if not text:
                flash('请输入要统计的文本内容')
                return redirect(request.url)

        # 获取关键词（按行分割）
        keywords_raw = request.form.get('keywords', '')
        keywords = [line.strip() for line in keywords_raw.splitlines() if line.strip()]
        if not keywords:
            flash('请输入至少一个关键词')
            return redirect(request.url)

        mode = request.form.get('mode', 'seg')
        categories_raw = request.form.get('categories', '')
        chart_option = (request.form.get('chart_option') or '').strip().lower()
        if chart_option not in ('pie', 'bar', 'line'):
            chart_option = ''
        generate_chart = chart_option in ('pie', 'bar', 'line')
        chart_type = chart_option if generate_chart else 'pie'

        # 计时并执行统计
        start = time.perf_counter()
        try:
            if mode == 'exact':
                results, total_words = count_exact(text, keywords)
            elif mode == 'seg':
                results, total_words = count_seg(text, keywords)
            else:  # search
                results, total_words = search_containing_words(text, keywords)
        except Exception as e:
            flash(f'处理出错：{e}')
            return redirect(request.url)
        elapsed = time.perf_counter() - start

        # 构建所有被统计词的字典（用于类别统计）
        if mode == 'search':
            # results 是 {kw: {word: cnt}} 形式，合并所有匹配词
            all_matched = {}
            for kw_matches in results.values():
                for word, cnt in kw_matches.items():
                    all_matched[word] = all_matched.get(word, 0) + cnt
            total_matched_occurrences = sum(all_matched.values())
        else:
            # exact/seg 模式，results 是 {word: cnt} 形式
            all_matched = results
            total_matched_occurrences = sum(results.values())

        # 解析类别并计算统计
        categories = parse_categories(categories_raw)
        category_stats = {}
        for cat_name, words in categories.items():
            cat_total = sum(all_matched.get(w, 0) for w in words)
            cat_percentage = (cat_total / total_matched_occurrences * 100) if total_matched_occurrences > 0 else 0
            category_stats[cat_name] = {'total': cat_total, 'percentage': cat_percentage}

        # 针对 search 模式，进行两列平衡分配（基于匹配词数量）
        left_items = []
        right_items = []
        if mode == 'search':
            # 构建列表，每个元素为 (keyword, matches, weight)
            items = []
            for kw, matches in results.items():
                weight = len(matches)  # 使用匹配词数量作为权重（每个词占一行）
                items.append((kw, matches, weight))
            # 按权重降序排序
            items.sort(key=lambda x: x[2], reverse=True)
            left_weight = 0
            right_weight = 0
            for kw, matches, w in items:
                if left_weight <= right_weight:
                    left_items.append((kw, matches))
                    left_weight += w
                else:
                    right_items.append((kw, matches))
                    right_weight += w
        else:
            # 非 search 模式，保持原有单列显示
            left_items = None
            right_items = None

        # 词频占比图数据：只按关键词占比，[关键词, 总次数] 列表
        chart_data = None
        if generate_chart and results:
            if mode == 'search':
                # 每个关键词下匹配词的总次数
                chart_data = [(kw, sum(matches.values())) for kw, matches in results.items()]
            else:
                # exact/seg：results 已是 关键词 -> 次数
                chart_data = list(results.items())
            chart_data = sorted(chart_data, key=lambda x: x[1], reverse=True)
            chart_data = [[k, c] for k, c in chart_data]

        return render_template('index.html',
                               mode=mode,
                               results=results,
                               total_words=total_words,
                               elapsed=elapsed,
                               keywords=keywords,
                               input_type=input_type,
                               direct_text=text if input_type == 'text' else '',
                               categories_raw=categories_raw,
                               category_stats=category_stats,
                               total_matched_occurrences=total_matched_occurrences,
                               left_items=left_items,
                               right_items=right_items,
                               generate_chart=generate_chart,
                               chart_type=chart_type,
                               chart_option=chart_option if generate_chart else '',
                               chart_data=chart_data)

    return render_template('index.html', mode=None, results=None, chart_option='pie')


if __name__ == '__main__':
    # 生产环境通常使用 Gunicorn 或其它 WSGI 服务器启动，
    # 下面的配置仅用于本地调试。
    import os
    port = int(os.environ.get('PORT', 5000))
    debug_mode = os.environ.get('FLASK_DEBUG', '1') == '1'
    app.run(host='0.0.0.0', port=port, debug=debug_mode)
