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
Trả về plain text. KHÔNG dùng markdown (không dùng **, __, ##, *, >, `).
Trình bày theo dòng: mỗi ý trong từng mục phải là một dòng riêng, bắt đầu bằng ký tự gạch đầu dòng `•` (dấu chấm tròn), sau đó một khoảng trắng rồi nội dung. Không dùng dấu `-` đầu dòng.

Dùng đúng khung sau (giữ số mục 1) 2) 3) 4) như tiêu đề phần; bên dưới mỗi tiêu đề là các dòng `• ...`):

1) Tóm tắt và trích xuất thông tin quan trọng
Dựa trên phần "Nội dung bài báo" (ưu tiên nội đã trích). Chia nhỏ thành 5 đến 12 dòng `•`, mỗi dòng một ý ngắn gọn: sự kiện chính; số liệu có trong bài (%, giá, kỳ so sánh…); bên liên quan; liên hệ với mã {symbol}. Nếu bài có nhiều luồng tin, dùng thêm vài dòng `•` để tách luồng.

2) Mức độ ảnh hưởng
Dòng đầu: ghi rõ Thấp / Trung bình / Cao. Tiếp theo 1 đến 3 dòng `•` giải thích lý do, chỉ dựa trên nội dung bài.

3) Điều cần theo dõi tiếp
3 đến 5 dòng `•`, mỗi dòng một ý riêng.

4) Rủi ro hoặc giả định
1 đến 3 dòng `•` (nếu không có thì ghi một dòng `• Không có rủi ro đặc biệt từ nội dung bài.`). Không gán cho bài sự kiện không có trong bài.
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
Trả về plain text. KHÔNG dùng markdown (không dùng **, __, ##, *, >, `).
Trình bày theo dòng: trong mỗi mục, mỗi ý là một dòng bắt đầu bằng `• ` (dấu chấm tròn + khoảng trắng). Không dùng dấu `-` đầu dòng.

Dùng đúng khung sau:

1) Phân tích sâu
8 đến 18 dòng `•`, bám sát dữ kiện trong bài; ưu tiên liệt kê số liệu, tên tổ chức, mốc thời gian có trong bài.

2) Tác động lên {symbol}
4 đến 8 dòng `•`, giải thích cơ chế ảnh hưởng, chỉ dựa trên nội dung bài.

3) Mốc thời gian và hạng mục cần theo dõi tiếp
4 đến 8 dòng `•`, mỗi dòng một ý.

4) Rủi ro hoặc giả định
2 đến 5 dòng `•`, không gán cho bài sự kiện không có trong bài.
""".strip()

        response = self._model.generate_content(
            prompt,
            generation_config={"max_output_tokens": 3072, "temperature": 0.2},
        )
        return (response.text or "").strip()

