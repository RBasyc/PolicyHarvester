import axios from "axios";
import * as cheerio from "cheerio";

export const getLatestPolicies = {
  name: "get_latest_policies",
  description: "Search State Council policies using gov.cn official search API with keyword search and date filtering.",
  parameters: {
    type: "object",
    properties: {
      startdate: {
        type: "string",
        description: "Start date in YYYY-MM-DD format"
      },
      enddate: {
        type: "string",
        description: "End date in YYYY-MM-DD format"
      },
      limit: {
        type: "number",
        description: "Max number of items to return (default: 100, max: 100)",
        default: 100
      },
      keyword: {
        type: "string",
        description: "Keyword to search in policy titles (searchfield=title; the only working value)"
      },
      agency: {
        type: "string",
        description: "Publishing agency filter (bmfl), e.g. 工业和信息化部. Only works on the current channel. Repeated values are silently ignored by the API — send one per call."
      },
      doc_type: {
        type: "string",
        description: "Document type filter (pcodeJiguan), one of 国令/国发/国函/国办发/国办函. Only affects the gongwen catalog."
      },
      page: {
        type: "number",
        description: "Result page number (default: 1), passed through as gov.cn search API param p. The request's n equals limit, so consecutive pages advance exactly limit rows. Used by the pipeline to keep fetching past page 1 when dedup leaves too few new items.",
        default: 1
      }
    },
    required: []
  },

  async run(args: { startdate?: string; enddate?: string; limit?: number; keyword?: string; agency?: string; doc_type?: string; page?: number }) {
    try {
      const limit = Math.max(1, Math.min(args.limit ?? 100, 100));
      // 规范化关键词：去除首尾空白与重复空白
      const normalizedKeyword = args.keyword?.trim().replace(/\s+/g, " ");
      const agency = args.agency?.trim();
      const docType = args.doc_type?.trim();
      // Always use the official search API. Keyword / filter-only ("filtered browse") /
      // fully-empty queries are all valid there (empty q = the library's browse list).
      // The old no-keyword branch scraped www.gov.cn/zhengce/zuixin/ (最新政策 HTML 列表) —
      // a different, smaller feed that silently ignores every filter. Wrong content: never use it.
      const { items, debug } = await searchPolicyAPI(normalizedKeyword || "", limit, args.startdate, args.enddate, agency, docType, args.page);
      return {
        content: [{
          type: "text",
          text: JSON.stringify({
            source: "gov_cn_search_api",
            search_method: "official_search_api",
            count: items.length,
            filters: {
              startdate: args.startdate,
              enddate: args.enddate,
              keyword: normalizedKeyword,
              agency,
              doc_type: docType,
              page: Math.max(1, Math.floor(args.page ?? 1))
            },
            items,
            debug
          }, null, 2)
        }]
      };
    } catch (error: any) {
      return {
        content: [{ type: "text", text: `❌ Failed to fetch policies: ${error.message}` }],
        isError: true
      };
    }
  }
};

// Use official gov.cn search API
async function searchPolicyAPI(keyword: string, limit: number, startdate?: string, enddate?: string, agency?: string, docType?: string, pageArg?: number) {
  const items: any[] = [];
  const requestedUrls: string[] = [];
  // 翻页续抓（Phase 13）：page 透传 gov.cn 的 p 参数（此前固定第一页）。越界页由 gov.cn
  // 以非 200 code / 空列表表达，调用方（pipeline）据「该组合返回 0 条」判定翻空。
  const page = Math.max(1, Math.floor(pageArg ?? 1));
  // n 必须跟随 limit：gov.cn 的 p 按 n 为步长翻页（p=1&n=100 → 第 1–100 行，p=2 → 第 101–200 行）。
  // 旧版 n 固定 100、返回前才截前 limit 条 → limit=10 时 p=1 取第 1–10 行、p=2 取第 101–110 行，
  // 第 11–100 行整段跳过（续抓中间漏一大块）。n = limit 后 p 连续递增时消费的行号连续；
  // catMap 多目录合并 + 截断仅在目录切换处最多漏 limit−1 条/目录（从「每页漏 ~90 行」收窄至此）。
  const pageSize = limit;

  try {
    // Build search API URL (n = limit, so consecutive p pages are contiguous)
    const searchUrl = "https://sousuo.www.gov.cn/search-gov/data";
    const params = new URLSearchParams({
      't': 'zhengcelibrary_gw_bm_gb',
      'q': keyword,
      // 'title' is the ONLY effective searchfield value — anything else silently disables
      // keyword filtering and returns the unfiltered browse list. (title:content:summary was a bug.)
      'searchfield': 'title',
      'sort': 'score',
      'sortType': '1',
      'p': page.toString(),
      'n': pageSize.toString(),
      'timetype': 'timezd'
    });

    // Add date range if provided
    if (startdate) params.append('mintime', startdate);
    if (enddate) params.append('maxtime', enddate);
    // Facet filters — only effective on the t=zhengcelibrary_gw_bm_gb channel.
    // The API silently IGNORES repeated values of these (bmfl=A&bmfl=B collapses to "no filter"),
    // so the caller must send exactly one value per call and fan out multi-select itself.
    if (agency) params.append('bmfl', agency);
    if (docType) params.append('pcodeJiguan', docType);

    const url = `${searchUrl}?${params.toString()}`;
    requestedUrls.push(url);

    const response = await axios.get(url, {
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://sousuo.www.gov.cn/"
      },
      timeout: 15000
    });

    // Parse JSON response (handle string payloads)
    const raw = response.data;
    let data: any;
    if (typeof raw === 'string') {
      try {
        data = JSON.parse(raw);
      } catch {
        data = raw; // keep as string for debug
      }
    } else {
      data = raw;
    }
    // gov.cn signals "0 results" with a NON-200 code (1001 = 抱歉，没有找到相关结果) and no
    // catMap at all — it is not rate limiting. Surface it instead of silently returning [],
    // so a genuine API error is never mistaken for "no policies matched".
    if (data && typeof data === "object" && String((data as any).code ?? "200") !== "200") {
      return {
        items: [],
        debug: {
          requested_urls: requestedUrls,
          api_code: String((data as any).code),
          api_msg: String((data as any).msg ?? "")
        }
      };
    }

    // Case 1: legacy/alternative structure with `results`
    if (data && Array.isArray(data.results)) {
      for (const result of data.results) {
        if (items.length >= pageSize) break;
        const policy = {
          policy_id: result.url || result.link || "",
          title: sanitizeHtmlText(result.title || ""),
          level: "state_council",
          category: "government_policy",
          date: formatDate(result.pubtime || result.publishtime || result.time || ""),
          source: "gov_cn_search_api",
          url: normalizeUrl(result.url || result.link || ""),
          summary: sanitizeHtmlText(result.summary || result.content || ""),
          issuing_agency: result.puborg || result.source || "国务院",
          document_number: result.pubnum || "",
          file_type: result.filetype || "html"
        };
        if (!policy.title || !policy.url || policy.title.length < 3) continue;
        items.push(policy);
      }
    }

    // Case 2: current official structure with `catMap.*.listVO` (parse regardless of results)
    const catMapDebug: Record<string, number> = {};
    // locate catMap at common locations
    const catMapCandidate: any = (data && (data.catMap || data?.searchVO?.catMap || data?.data?.catMap)) || undefined;
    if (catMapCandidate && typeof catMapCandidate === "object") {
      const catEntries: Array<[string, any]> = Object.entries(catMapCandidate).filter(([, v]: any) => v && Array.isArray(v.listVO));
      for (const [catKey, cat] of catEntries) {
        catMapDebug[catKey] = Array.isArray(cat.listVO) ? cat.listVO.length : 0;
        // 公报 (gongbao) rows ignore every facet filter — bmfl / pcodeJiguan included; they come
        // back as the same large block (≈12–56 rows) no matter what the caller asked for. With a
        // filter active they are pure noise, and because the caller only ever sees the first
        // `limit` items they can also crowd out the genuine matches. Drop them HERE, before the
        // slice, so `limit` is spent on rows that actually satisfy the filter. (The pipeline
        // repeats this check as a safety net; keep the behaviour in sync.)
        if ((agency || docType) && catKey === "gongbao") continue;
        for (const result of cat.listVO) {
          if (items.length >= pageSize) break;
          const url2 = normalizeUrl(result.url || result.link || "");
          const title = sanitizeHtmlText((result.title || "").toString());

          // Date precedence matters. gov.cn's date window filters on the 发布日期 it DISPLAYS
          // (ptimeStr / pubtimeStr). `ptime` is an epoch that is frequently an EARLIER 成文日期 —
          // e.g. a 公报 article shown as 2026.10.10 carries ptime 2026-08-28. Preferring the
          // epoch made the pipeline's "date must fall inside the window" re-check discard rows
          // the API had legitimately returned for that window: the agency facet showed 1 match,
          // yet the run failed with 未检索到匹配的政策原文. Text date first; epoch only for
          // records that omit the text date (the API omits ptimeStr on some query shapes).
          const date = formatDate(result.ptimeStr || result.pubtimeStr || result.publishtime || result.time || "")
            || formatDateFromEpoch(result.ptime || result.pubtime);

          const policy = {
            policy_id: url2,
            title: title,
            level: "state_council",
            category: "government_policy",
            date: date,
            source: "gov_cn_search_api",
            url: url2,
            summary: sanitizeHtmlText(result.summary || result.content || ""),
            issuing_agency: result.puborg || result.source || "国务院",
            document_number: result.pcode || result.pubnum || "",
            file_type: result.filetype || result.wjlx || "html",
            // catalog_key = the catMap key (e.g. "gongwen"/"bumenfile"/"gongbao") — the reliable
            // field for server-side filtering; cat.catName is a fallback display name.
            catalog_key: catKey,
            catalog: cat.catName || undefined
          };

          if (!policy.title || !policy.url || policy.title.length < 3) continue;
          items.push(policy);
        }
        if (items.length >= pageSize) break;
      }
    }

    return { items: items.slice(0, limit), debug: { requested_urls: requestedUrls, page_size: pageSize, page, catmap_list_lengths: catMapDebug, raw_type: typeof raw } };

  } catch (error) {
    console.error(`Error searching API page ${page}:`, error);
    try {
      const fallbackItems = await searchPolicyFallback(keyword, Math.min(limit, 20));
      return { items: fallbackItems, debug: { requested_urls: requestedUrls, used_fallback: true } };
    } catch (fallbackError) {
      console.error("Fallback method also failed:", fallbackError);
      return { items: [], debug: { requested_urls: requestedUrls, error: String(error) } };
    }
  }
}

function sanitizeHtmlText(value: any): string {
  if (!value) return "";
  try {
    const $ = cheerio.load(`<div>${value}</div>`);
    return $('div').text().trim();
  } catch {
    return String(value).replace(/<[^>]*>/g, '').trim();
  }
}

// facets removed

// Fallback method using HTML parsing
async function searchPolicyFallback(keyword: string, limit: number) {
  try {
    const encodedKeyword = encodeURIComponent(keyword);
    const searchUrl = `https://sousuo.www.gov.cn/zcwjk/policyDocumentLibrary?t=zhengcelibrary&q=${encodedKeyword}`;
    
    const response = await axios.get(searchUrl, {
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
      },
      timeout: 10000
    });
    
    const $ = cheerio.load(response.data);
    const items: any[] = [];
    
    // Try different selectors for search results
    const selectors = [".result-item", ".search-result", ".policy-item", ".list-item"];
    
    for (const selector of selectors) {
      $(selector).each((_, element) => {
        if (items.length >= limit) return false;
        
        const $item = $(element);
        const $link = $item.find("a").first();
        const title = $link.text().trim();
        const url = normalizeUrl($link.attr("href") || "");
        
        if (!title || !url || title.length < 3) return;
        
        // Extract date
        const dateText = $item.text();
        const date = extractDateFromText(dateText);
        
        items.push({
          policy_id: url,
          title: title,
          level: "state_council",
          category: "government_policy",
          date: date,
          source: "gov_cn_fallback",
          url: url,
          summary: $item.find(".summary, .description").text().trim() || ""
        });
      });
      
      if (items.length > 0) break;
    }
    
    return items;
  } catch (error) {
    console.error("Fallback method failed:", error);
    return [];
  }
}

// Helper functions
function formatDate(dateStr: string): string {
  if (!dateStr) return "";
  
  // Try different date formats
  const patterns = [
    /(\d{4})-(\d{1,2})-(\d{1,2})/,  // YYYY-MM-DD
    /(\d{4})年(\d{1,2})月(\d{1,2})日/,  // YYYY年MM月DD日
    /(\d{4})\/(\d{1,2})\/(\d{1,2})/,   // YYYY/MM/DD
    /(\d{4})\.(\d{1,2})\.(\d{1,2})/   // YYYY.MM.DD
  ];
  
  for (const pattern of patterns) {
    const match = dateStr.match(pattern);
    if (match) {
      const [_, year, month, day] = match;
      return `${year}-${month.padStart(2, '0')}-${day.padStart(2, '0')}`;
    }
  }
  
  return dateStr;
}

function formatDateFromEpoch(epochMaybe: unknown): string {
  if (typeof epochMaybe === "number" && isFinite(epochMaybe)) {
    const d = new Date(epochMaybe);
    if (!isNaN(d.getTime())) return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  }
  if (typeof epochMaybe === "string" && /^(\d{10}|\d{13})$/.test(epochMaybe)) {
    const num = epochMaybe.length === 10 ? Number(epochMaybe) * 1000 : Number(epochMaybe);
    const d = new Date(num);
    if (!isNaN(d.getTime())) return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  }
  return "";
}

function normalizeUrl(url: string): string {
  if (!url) return "";
  
  if (url.startsWith("//")) {
    return "https:" + url;
  } else if (url.startsWith("/")) {
    return "https://www.gov.cn" + url;
  } else if (!/^https?:\/\//i.test(url)) {
    return "https://www.gov.cn/" + url;
  }
  
  return url;
}

function extractDateFromText(text: string): string {
  const dateMatch = text.match(/(20\d{2})[年\-](\d{1,2})[月\-](\d{1,2})[日]?/);
  if (dateMatch) {
    const [_, y, m, d] = dateMatch;
    return `${y}-${m.padStart(2, "0")}-${d.padStart(2, "0")}`;
  }
  return "";
}