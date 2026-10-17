
import hashlib
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

BASE = Path(__file__).resolve().parent
DB = BASE / "data" / "jobs.db"
EXPORTS = BASE / "exports"

ROLES = ["行政", "文员", "运营", "助理", "数据整理", "其他"]
STATUSES = ["待审核", "待联系", "已联系", "已回复", "已拒绝", "已结束"]

# 可根据实际求职方向调整
PREFERRED = [
    "资料整理", "文档处理", "数据统计", "报表",
    "信息录入", "沟通协调", "档案管理", "文件归档",
    "活动策划", "通知发布", "客户沟通", "数据分析",
]

RISK_WORDS = [
    "电话销售", "销售提成", "先交费", "培训贷",
    "刷单", "垫付",
]

st.set_page_config(
    page_title="求职 Agent",
    page_icon="������",
    layout="wide",
)

DB.parent.mkdir(parents=True, exist_ok=True)
EXPORTS.mkdir(parents=True, exist_ok=True)


def connect():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                company TEXT NOT NULL,
                title TEXT NOT NULL,
                salary TEXT DEFAULT '',
                location TEXT DEFAULT '',
                description TEXT DEFAULT '',
                url TEXT DEFAULT '',
                score INTEGER DEFAULT 0,
                reasons TEXT DEFAULT '',
                warnings TEXT DEFAULT '',
                status TEXT DEFAULT '待审核',
                draft TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)


def make_id(company, title, url):
    # 优先使用岗位链接；没有链接时使用公司+岗位名称
    key = (url or "").strip()
    if not key:
        key = f"{company.strip()}|{title.strip()}"
    return hashlib.sha256(
        key.lower().encode("utf-8")
    ).hexdigest()[:20]


def evaluate(title, description):
    title = str(title or "").lower()
    text = (title + " " + str(description or "")).lower()

    score = 0
    reasons = []

    role_hits = [
        role for role in ROLES[:-1]
        if role.lower() in title
    ]
    if role_hits:
        score += 25
        reasons.append("岗位方向：" + "、".join(role_hits))

    matches = [word for word in PREFERRED if word.lower() in text]
    score += min(len(matches) * 10, 50)

    if matches:
        reasons.append("技能匹配：" + "、".join(matches))

    warnings = [word for word in RISK_WORDS if word.lower() in text]
    if warnings:
        score -= 30
        reasons.append("风险词需核查：" + "、".join(warnings))

    score = max(0, min(score, 100))

    if not role_hits and not matches:
        reasons.append("匹配依据不足，建议人工查看")

    return score, "；".join(reasons), "、".join(warnings)


def make_draft(title, company, description):
    text = str(description or "").lower()
    matches = [word for word in PREFERRED if word.lower() in text]
    relevant = "、".join(matches[:3])

    if relevant:
        experience = (
            f"我有资料整理、报表处理及沟通协调等相关经历，"
            f"与岗位中提到的{relevant}有一定匹配。"
        )
    else:
        experience = (
            "我有文档整理、数据处理和沟通协调方面的相关经历，"
            "希望进一步了解岗位要求。"
        )

    return (
        f"您好，我想了解一下贵公司{title}岗位。"
        f"{experience}"
        f"方便的话，想进一步了解岗位的主要工作内容及任职要求，"
        f"谢谢！"
    )


def add_job(job):
    company = str(job.get("company", "") or "").strip()
    title = str(job.get("title", "") or "").strip()

    if not company or not title:
        return False, "公司名称和岗位名称不能为空"

    description = str(job.get("description", "") or "")
    url = str(job.get("url", "") or "").strip()
    salary = str(job.get("salary", "") or "")
    location = str(job.get("location", "") or "")

    score, reasons, warnings = evaluate(title, description)
    job_id = make_id(company, title, url)
    draft = make_draft(title, company, description)

    with connect() as conn:
        cursor = conn.execute("""
            INSERT OR IGNORE INTO jobs (
                job_id, company, title, salary, location,
                description, url, score, reasons, warnings, draft
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            job_id, company, title, salary, location,
            description, url, score, reasons, warnings, draft
        ))

    if cursor.rowcount == 0:
        return False, "疑似重复岗位，已跳过"
    return True, "岗位已保存"


def get_jobs():
    with connect() as conn:
        return pd.read_sql_query(
            "SELECT * FROM jobs ORDER BY score DESC, created_at DESC",
            conn,
        )


def update_job(job_id, status, draft):
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET status=?, draft=? WHERE job_id=?",
            (status, draft, job_id),
        )


init_db()

st.title("������ 求职 Agent")
st.caption("本地岗位筛选、消息草稿与求职进度管理")

jobs = get_jobs()
total = len(jobs)

c1, c2, c3, c4 = st.columns(4)
c1.metric("岗位总数", total)
c2.metric("待审核", int((jobs["status"] == "待审核").sum()) if total else 0)
c3.metric("待联系", int((jobs["status"] == "待联系").sum()) if total else 0)
c4.metric("已联系", int(jobs["status"].isin(["已联系", "已回复"]).sum()) if total else 0)

tab_add, tab_list, tab_export = st.tabs([
    "① 添加岗位", "② 筛选与管理", "③ 导入与导出"
])

with tab_add:
    st.subheader("手动录入岗位")
    st.write(
        "在浏览器中正常查看招聘信息，将岗位内容复制到这里。"
        "第一版不自动抓取网页，也不操作平台的发送按钮。"
    )

    with st.form("job_form", clear_on_submit=True):
        company = st.text_input("公司名称 *")
        title = st.text_input("岗位名称 *")
        salary = st.text_input("薪资范围", placeholder="例如：5k-7k")
        location = st.text_input("工作地点", placeholder="例如：上海")
        url = st.text_input("岗位链接", placeholder="可选")
        description = st.text_area(
            "岗位职责与要求",
            height=180,
            placeholder="粘贴招聘详情中的岗位职责、任职要求等内容",
        )
        submitted = st.form_submit_button("分析并保存岗位")

    if submitted:
        ok, message = add_job({
            "company": company,
            "title": title,
            "salary": salary,
            "location": location,
            "url": url,
            "description": description,
        })
        if ok:
            st.success(message)
            st.rerun()
        else:
            st.warning(message)

with tab_list:
    st.subheader("岗位筛选与跟进")

    jobs = get_jobs()
    if jobs.empty:
        st.info("暂时没有岗位。先在“添加岗位”中录入，或导入 CSV。")
    else:
        min_score = st.slider("最低匹配分数", 0, 100, 30)
        selected_status = st.multiselect(
            "处理状态",
            STATUSES,
            default=STATUSES,
        )
        keyword = st.text_input("搜索公司、岗位或关键词")

        filtered = jobs[jobs["score"] >= min_score]
        filtered = filtered[filtered["status"].isin(selected_status)]

        if keyword:
            mask = (
                filtered["company"].fillna("").str.contains(
                    keyword, case=False, regex=False
                )
                | filtered["title"].fillna("").str.contains(
                    keyword, case=False, regex=False
                )
                | filtered["description"].fillna("").str.contains(
                    keyword, case=False, regex=False
                )
            )
            filtered = filtered[mask]

        st.dataframe(
            filtered[[
                "company", "title", "salary", "location",
                "score", "status", "reasons"
            ]],
            use_container_width=True,
            hide_index=True,
        )

        st.divider()
        st.subheader("查看岗位与编辑招呼语")

        for _, row in filtered.iterrows():
            label = (
                f'{row["score"]}分｜{row["company"]}｜'
                f'{row["title"]}'
            )

            with st.expander(label):
                st.write("**匹配理由：**", row["reasons"] or "暂无")
                if row["warnings"]:
                    st.warning("风险词提示：" + row["warnings"])

                if row["url"]:
                    st.markdown(f'[打开岗位链接]({row["url"]})')

                st.write("**岗位描述**")
                st.text(row["description"] or "未录入")

                with st.form(f'edit_{row["job_id"]}'):
                    status = st.selectbox(
                        "跟进状态",
                        STATUSES,
                        index=(
                            STATUSES.index(row["status"])
                            if row["status"] in STATUSES else 0
                        ),
                    )
                    draft = st.text_area(
                        "招呼语草稿（确认内容后自行发送）",
                        value=row["draft"] or "",
                        height=140,
                    )
                    save = st.form_submit_button("保存状态与草稿")

                if save:
                    update_job(row["job_id"], status, draft)
                    st.success("已保存")
                    st.rerun()

with tab_export:
    st.subheader("CSV 导入")
    st.write(
        "CSV 需要包含 company、title 字段；"
        "可选字段：salary、location、description、url。"
    )

    sample = pd.DataFrame([{
        "company": "示例公司",
        "title": "行政文员",
        "salary": "5k-7k",
        "location": "上海",
        "description": "负责资料整理、报表、信息录入和沟通协调",
        "url": "https://example.com/job/123",
    }])

    st.download_button(
        "下载 CSV 模板",
        sample.to_csv(index=False).encode("utf-8-sig"),
        file_name="jobs_template.csv",
        mime="text/csv",
    )

    uploaded = st.file_uploader("导入岗位 CSV", type=["csv"])
    if uploaded is not None:
        try:
            incoming = pd.read_csv(uploaded).fillna("")
            required = {"company", "title"}
            if not required.issubset(incoming.columns):
                st.error("缺少必需字段 company 或 title")
            else:
                st.dataframe(incoming.head(10), use_container_width=True)
                if st.button("确认导入 CSV"):
                    added = 0
                    skipped = 0
                    for _, item in incoming.iterrows():
                        ok, _ = add_job(item.to_dict())
                        added += int(ok)
                        skipped += int(not ok)
                    st.success(
                        f"导入完成：新增 {added} 条，跳过 {skipped} 条。"
                    )
                    st.rerun()
        except Exception as exc:
            st.error(f"读取失败，请检查 CSV 编码和格式：{exc}")

    st.divider()
    st.subheader("导出全部岗位")
    jobs = get_jobs()

    if not jobs.empty:
        csv_data = jobs.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "导出岗位与跟进记录",
            csv_data,
            file_name="job_agent_export.csv",
            mime="text/csv",
        )
    else:
        st.info("暂无数据可导出。")

st.divider()
st.caption(
    "数据默认保存在本机 data/jobs.db。"
    "请自行核实岗位真实性、招聘条件与平台规则。"
)
