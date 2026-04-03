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
Hãy phân tích tin sau (nếu là tiếng Anh hãy dịch và trình bày bằng tiếng Việt) và đánh giá ảnh hưởng đến cổ phiếu {symbol} {f'({company})' if company else ''}.

Tiêu đề: {title}
Tóm tắt/RSS snippet (nếu có): {strip_html(snippet_html)}
Nội dung bài báo (đã trích): {article_text or '[Không trích được nội dung, hãy dựa trên tiêu đề và snippet]'}
Link: {source_url}

Yêu cầu output (Tiếng Việt, rõ ràng, đủ thông tin):
Trả về plain text, KHÔNG dùng markdown (không dùng **, __, ##, *, -, >, `).
Dùng đúng cấu trúc sau (mỗi mục bắt đầu bằng số và dấu ngoặc như mẫu):

1) Tóm tắt và trích xuất thông tin quan trọng:
Dựa trên toàn bộ phần "Nội dung bài báo" ở trên (ưu tiên nội dung đã trích, không chỉ tiêu đề), hãy nêu đầy đủ:
sự kiện/diễn biến chính; các số liệu cụ thể nếu có (doanh thu, lợi nhuận, %, giá, khối lượng, mức tăng giảm, thời hạn, địa điểm);
các bên liên quan (công ty, cơ quan, nhân vật được nhắc); cam kết, kế hoạch, chính sách hoặc trích dẫn đáng chú ý;
và mối liên hệ trực tiếp hoặc gián tiếp với mã {symbol}.
Viết khoảng 4 đến 10 câu (hoặc tương đương), đủ để người đọc nắm các điểm then chốt trong bài mà không bị lược quá mức. Nếu bài có nhiều luồng thông tin, nêu rõ từng luồng.

2) Mức độ ảnh hưởng: Thấp / Trung bình / Cao. Nêu ngắn gọn lý do.

3) Điều cần theo dõi tiếp: 3 đến 5 ý, ngăn cách bằng dấu chấm phẩy (;).

4) Rủi ro/giả định: 1 đến 3 ý ngắn (nếu có).
""".strip()

        response = self._model.generate_content(
            prompt,
            generation_config={"max_output_tokens": 2048, "temperature": 0.35},
        )
        return (response.text or "").strip()

