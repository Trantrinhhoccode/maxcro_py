from __future__ import annotations

from dataclasses import dataclass

from .text import strip_html


@dataclass
class GeminiAnalyzer:
    api_key: str
    model_name: str

    def __post_init__(self) -> None:
        import google.generativeai as genai

        genai.configure(api_key=self.api_key)
        self._model = genai.GenerativeModel(self.model_name)

    def analyze(
        self,
        *,
        symbol: str,
        company: str,
        title: str,
        snippet_html: str,
        article_text: str,
        source_url: str,
    ) -> str:
        prompt = f"""
Bạn là chuyên gia phân tích doanh nghiệp/chứng khoán Việt Nam.
Hãy phân tích tin sau (nếu là tiếng Anh hãy dịch và tóm tắt bằng tiếng Việt) và đánh giá ảnh hưởng đến cổ phiếu {symbol} {f'({company})' if company else ''}.

Tiêu đề: {title}
Tóm tắt/RSS snippet (nếu có): {strip_html(snippet_html)}
Nội dung bài báo (đã trích): {article_text or '[Không trích được nội dung, hãy dựa trên tiêu đề và snippet]'}
Link: {source_url}

Yêu cầu output (Tiếng Việt, ngắn gọn, rõ ràng):
1) 🧾 **Tóm tắt 1-2 câu**
2) 🎯 **Ảnh hưởng tới doanh nghiệp/cổ phiếu**: Tích cực / Trung tính / Tiêu cực
3) 📈 **Mức độ ảnh hưởng**: Thấp / Trung bình / Cao (kèm lý do)
4) 🔎 **Điều cần theo dõi tiếp**: 2-3 bullet
5) ⚠️ **Rủi ro/giả định**: 1-2 bullet (nếu có)
""".strip()

        response = self._model.generate_content(prompt)
        return (response.text or "").strip()

