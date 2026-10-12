import axios from "axios";

// 返回 gov.cn 检索接口的分面（facet）数据：发布机构分类与文种的可选值 + 条数。
// 这些值是「按当前检索条件统计」的（不是全库静态列表），因此必须随关键词/日期查询。
export const getPolicyFacets = {
  name: "get_policy_facets",
  description: "List available publishing agencies (bmfl) and document types (pcodeJiguan) for the current search, with per-value document counts. Use this to populate filter dropdowns.",
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
      keyword: {
        type: "string",
        description: "Keyword to scope the facets by (same title search as get_latest_policies)"
      }
    },
    required: []
  },

  async run(args: { startdate?: string; enddate?: string; keyword?: string }) {
    try {
      const normalizedKeyword = args.keyword?.trim().replace(/\s+/g, " ") || "";
      const searchUrl = "https://sousuo.www.gov.cn/search-gov/data";
      const params = new URLSearchParams({
        't': 'zhengcelibrary_gw_bm_gb',
        'q': normalizedKeyword,
        'searchfield': 'title',
        'sort': 'score',
        'sortType': '1',
        'p': '1',
        'n': '5',
        'timetype': 'timezd'
      });
      if (args.startdate) params.append('mintime', args.startdate);
      if (args.enddate) params.append('maxtime', args.enddate);

      const url = `${searchUrl}?${params.toString()}`;
      const response = await axios.get(url, {
        headers: {
          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
          "Accept": "application/json, text/plain, */*",
          "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
          "Referer": "https://sousuo.www.gov.cn/"
        },
        timeout: 15000
      });

      const raw = response.data;
      let data: any;
      if (typeof raw === 'string') {
        try { data = JSON.parse(raw); } catch { data = null; }
      } else {
        data = raw;
      }

      // searchVO lives at the top level of this response (data.data is null)
      const extendresult = data?.searchVO?.extendresult || data?.extendresult || {};
      const facetMap = extendresult.facetMap || {};
      const groupMap = extendresult.groupMap || {};

      // bmfl values look like "76条"; groupMap values are plain ints.
      const agencies = Object.entries(facetMap.bmfl || {})
        .filter(([k]) => k !== 'count')
        .map(([name, count]) => ({ name, count: parseInt(String(count).replace(/条/g, ''), 10) || 0 }))
        .sort((a, b) => b.count - a.count);
      const docTypes = Object.entries(groupMap)
        .filter(([k]) => k !== 'count')
        .map(([name, count]) => ({ name, count: typeof count === 'number' ? count : (parseInt(String(count), 10) || 0) }))
        .sort((a, b) => b.count - a.count);

      return {
        content: [{
          type: "text",
          text: JSON.stringify({
            source: "gov_cn_search_api",
            keyword: normalizedKeyword || null,
            agencies,
            doc_types: docTypes
          }, null, 2)
        }]
      };
    } catch (error: any) {
      return {
        content: [{ type: "text", text: `❌ Failed to fetch facets: ${error.message}` }],
        isError: true
      };
    }
  }
};
