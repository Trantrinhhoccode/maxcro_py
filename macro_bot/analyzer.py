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

QUY TẮC BẮT BUỘC (tránh bịa nội dung):
Chỉ được mô tả sự kiện, số liệu, tên tổ chức, người, dự báo, %, mức tăng giảm, kỳ so sánh khi chúng xuất hiện trong phần "Nội dung bài báo" (hoặc trong tiêu đề/snippet nếu không có nội dung đã trích).
Cấm tuyệt đối thêm thông tin không có trong bài: ví dụ sự kiện chính trị, đại hội đảng, chính sách vĩ mô, hay bất kỳ chủ đề nào không được bài viết nhắc tới.
Nếu bài có nêu con số cụ thể (ví dụ dự báo giá thép quý 2, % tăng, biên lợi nhuận): phải trích đúng số và ngữ cảnh (ai nói, so với cùng kỳ hay gì). Không được nói "bài không có số liệu" nếu trong đoạn trích thực sự có số.
Không lấp đoạn bằng khẩu hiệu chung chung hoặc kiến thức bên ngoài bài.

Yêu cầu output (Tiếng Việt, rõ ràng, đủ thông tin):
Trả về plain text, KHÔNG dùng markdown (không dùng **, __, ##, *, -, >, `).
Dùng đúng cấu trúc sau (mỗi mục bắt đầu bằng số và dấu ngoặc như mẫu):

1) Tóm tắt và trích xuất thông tin quan trọng:
Dựa trên toàn bộ phần "Nội dung bài báo" ở trên (ưu tiên nội dung đã trích, không chỉ tiêu đề), hãy nêu đầy đủ:
sự kiện/diễn biến chính; các số liệu cụ thể nếu có (doanh thu, lợi nhuận, %, giá, khối lượng, mức tăng giảm, thời hạn, địa điểm);
các bên liên quan (công ty, cơ quan, nhân vật được nhắc); cam kết, kế hoạch, chính sách hoặc trích dẫn đáng chú ý;
và mối liên hệ trực tiếp hoặc gián tiếp với mã {symbol}.
Viết khoảng 4 đến 10 câu (hoặc tương đương), đủ để người đọc nắm các điểm then chốt trong bài mà không bị lược quá mức. Nếu bài có nhiều luồng thông tin, nêu rõ từng luồng.

2) Mức độ ảnh hưởng: Thấp / Trung bình / Cao. Nêu ngắn gọn lý do, chỉ dựa trên nội dung bài.

3) Điều cần theo dõi tiếp: 3 đến 5 ý, ngăn cách bằng dấu chấm phẩy (;).

4) Rủi ro/giả định: 1 đến 3 ý ngắn (nếu có), có thể là rủi ro thị trường chung nhưng không được gán cho bài những sự kiện không có trong bài.
""".strip()

        response = self._model.generate_content(
            prompt,
            generation_config={"max_output_tokens": 2048, "temperature": 0.2},
        )
        return (response.text or "").strip()

    def deep_dive(
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
Hãy tạo bản phân tích DEEP DIVE chi tiết hơn (dựa ưu tiên vào toàn bộ "Nội dung bài báo") và đánh giá ảnh hưởng đến cổ phiếu {symbol} {f'({company})' if company else ''}.

Tiêu đề: {title}
Snippet/RSS snippet (nếu có): {strip_html(snippet_html)}
Nội dung bài báo (đã trích): {article_text or '[Không trích được nội dung, hãy dựa trên tiêu đề và snippet]'}
Link: {source_url}

QUY TẮC BẮT BUỘC (tránh bịa nội dung):
Chỉ được dùng thông tin có trong "Nội dung bài báo" (hoặc tiêu đề/snippet khi không trích được bài). Không được thêm sự kiện chính trị, đại hội đảng, hay chủ đề không xuất hiện trong bài.
Mọi con số, %, dự báo, so sánh cùng kỳ phải lấy từ bài; nếu bài có nhiều số thì ưu tiên liệt kê đầy đủ các số liên quan trực tiếp tới {symbol} và ngành liên quan trong bài.
Phần suy luận chỉ được mở rộng từ dữ kiện đã có trong bài, không đưa "kiến thức nền" làm sự kiện đã xảy ra.

Yêu cầu output (Tiếng Việt, rõ ràng, đủ thông tin):
Trả về plain text, KHÔNG dùng markdown (không dùng **, __, ##, *, -, >, `).

Dùng đúng cấu trúc sau:
1) Phân tích sâu: 6-14 câu, bám sát dữ kiện; trích đầy đủ số liệu có trong bài (nếu có).
2) Tác động lên {symbol}: giải thích cơ chế ảnh hưởng (ngắn gọn nhưng cụ thể), chỉ dựa trên nội dung bài.
3) Mốc thời gian & hạng mục cần theo dõi tiếp: 3-6 ý, ngăn cách bằng dấu chấm phẩy (;).
4) Rủi ro/giả định: 1-3 ý ngắn, không gán cho bài sự kiện không có trong bài.
""".strip()

        response = self._model.generate_content(
            prompt,
            generation_config={"max_output_tokens": 3072, "temperature": 0.2},
        )
        return (response.text or "").strip()

