import axios from "axios";
import * as cheerio from "cheerio";
export const getPolicyFulltext = {
    name: "get_policy_fulltext",
    description: "Fetch policy full text and normalize into structured JSON (title, date, doc_no, body, issuer). Optimized for gov.cn pages.",
    parameters: {
        type: "object",
        properties: {
            url: { type: "string", description: "Policy page URL" },
            policy_id: { type: "string", description: "Optional internal policy id" }
        },
        required: ["url"]
    },
    async run(args) {
        try {
            if (!args.url)
                throw new Error("url is required");
            const html = await fetchHtml(args.url);
            const data = parseGovCnPolicyHtml(html, args.url);
            return {
                content: [{ type: "text", text: JSON.stringify(data, null, 2) }]
            };
        }
        catch (error) {
            return {
                content: [{ type: "text", text: `❌ Failed: ${error.message}` }],
                isError: true
            };
        }
    }
};
async function fetchHtml(url) {
    const res = await axios.get(url, {
        responseType: "text",
        headers: {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        },
        timeout: 20000
    });
    return res.data;
}
function parseGovCnPolicyHtml(html, url) {
    const $ = cheerio.load(html);
    // gov.cn 政策库详情页的元信息表：<div class="policyLibraryOverview_content"> 里的 table，
    // <td> 为「标 题：/发文机关：/发文字号：/来 源：/主题分类：/公文种类：/成文日期：」标签值对
    // （标签里可能带全角空格）。解析成归一化 meta map，作为 title/issuer/doc_no/date 的首选来源。
    const headMeta = extractOverviewMeta($);
    // Title extraction：优先元信息表的「标题」，其次页面 <title>（剥掉 _中国政府网 一类站点后缀）
    const rawTitle = headMeta["标题"] ||
        $("h1").first().text().trim() ||
        $(".article .title").first().text().trim() ||
        $("title").first().text().trim();
    const title = rawTitle.replace(/_.{0,12}中国政府网\s*$/, "").trim();
    // Date extraction
    let date = headMeta["成文日期"] ||
        $("time").attr("datetime") ||
        $(".meta .date").first().text().trim() ||
        $(".date").first().text().trim() ||
        extractDate($("body").text());
    // Issuer extraction
    const issuer = headMeta["发文机关"] ||
        $(".source, .publisher, .department, .origin").first().text().trim() ||
        inferIssuerFromText($("body").text());
    // Document number extraction (e.g., 国发〔2023〕X号)
    const docNo = headMeta["发文字号"] ||
        $(".doc-no, .docnum, .docNo").first().text().trim() ||
        extractDocNo($("body").text());
    // Body extraction —— 真正的正文容器是 div#UCAP-CONTENT（.pages_content）。
    // 旧顺序 article→.content→.article 在 gov.cn 页面全部落空，会兜底到整页 body 文本，
    // 把 CSS/JS/导航噪声存进正文。
    // **容器命中即用其文本；只有整页没有任何已知容器（未知布局站点）才兜底整页 body**——
    // 图片版文件（红头文件扫描页）容器存在但 .text() 为空，若再兜底整页，导航/脚本/元信息
    // 组成的页面壳就成了「正文」，下游清洗的 5% 安全网反而会保住这份垃圾（2026-10-13
    // 医保发〔2026〕19号 漏网案例）。image_count 一并上报，供服务端把这类页面明确标为图片版。
    const containerSelectors = ["div#UCAP-CONTENT", ".pages_content", "article", ".content", ".article"];
    let body = "";
    let imageCount = 0;
    for (const sel of containerSelectors) {
        const $el = $(sel).first();
        if (!$el.length)
            continue;
        const text = $el.text().trim();
        if (text) {
            body = text;
            imageCount = $el.find("img").length;
            break;
        }
        // 容器存在但无文本：记下其中的图片数（图片版文件），继续找下一个容器
        imageCount = Math.max(imageCount, $el.find("img").length);
    }
    if (!body && !imageCount) {
        body = $("body").text().trim();
    }
    return {
        policy_id: url,
        title,
        date: normalizeDate(date),
        doc_no: docNo,
        issuer,
        url,
        body,
        // 命中的正文容器内 <img> 数量（图片版文件 >0 且 body 为空；整页兜底时恒 0），
        // 供服务端把「抓不到文本」的页面明确标为图片版
        image_count: imageCount,
        // 元信息表里的补充字段（服务端按名取用，缺省时为 ""）
        source: headMeta["来源"] || "",
        category: headMeta["主题分类"] || "",
        doc_kind: headMeta["公文种类"] || ""
    };
}
/** 解析政策库 overview 元信息表：把 <td> 标签值对归一化成 { 发文机关: "…", … }。 */
function extractOverviewMeta($) {
    const meta = {};
    const $table = $("div.policyLibraryOverview_content table").first();
    if (!$table.length)
        return meta;
    const cells = $table
        .find("td")
        .map((_, td) => ($(td)
        .text()
        .replace(/ /g, " ") || "")
        .trim())
        .get();
    for (let i = 0; i < cells.length; i++) {
        const label = cells[i].replace(/[\s　]+/g, "");
        if (!/^(标　*题|发文机关|发文字号|来　*源|主题分类|公文种类|成文日期)：$/.test(label))
            continue;
        const key = label.replace(/：$/, "");
        // 标签的下一个 td 是对应的值
        meta[key] = (cells[i + 1] || "").trim();
    }
    return meta;
}
function extractDate(text) {
    const m = text.match(/(20\d{2})[年\-\.\/]\s?(\d{1,2})[月\-\.\/]\s?(\d{1,2})[日]?/);
    if (!m)
        return "";
    const [_, y, mo, d] = m;
    return `${y}-${mo.padStart(2, "0")}-${d.padStart(2, "0")}`;
}
function normalizeDate(s) {
    if (!s)
        return "";
    const m = s.match(/(20\d{2})[-./](\d{1,2})[-./](\d{1,2})/);
    if (m)
        return `${m[1]}-${m[2].padStart(2, "0")}-${m[3].padStart(2, "0")}`;
    const cn = s.match(/(20\d{2})年(\d{1,2})月(\d{1,2})日/);
    if (cn)
        return `${cn[1]}-${cn[2].padStart(2, "0")}-${cn[3].padStart(2, "0")}`;
    return s.trim();
}
function extractDocNo(text) {
    const patterns = [
        /([\u4e00-\u9fa5]{2,4}\s?发\s?〔\s?20\d{2}\s?〕\s?\d+\s?号)/, // 国发〔2023〕X号
        /(银发\s?〔\s?20\d{2}\s?〕\s?\d+\s?号)/,
        /(财税\s?〔\s?20\d{2}\s?〕\s?\d+\s?号)/
    ];
    for (const p of patterns) {
        const m = text.match(p);
        if (m)
            return m[1].replace(/\s+/g, "");
    }
    return "";
}
function inferIssuerFromText(text) {
    const patterns = [
        /(国务院)/,
        /(全国人民代表大会)/,
        /(中国人民银行)/,
        /(国家发展和改革委员会)/,
        /(财政部)/
    ];
    for (const p of patterns) {
        const m = text.match(p);
        if (m)
            return m[1];
    }
    return "";
}
