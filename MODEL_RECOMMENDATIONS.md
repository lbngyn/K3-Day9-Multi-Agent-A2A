# Model đề xuất cho OpenRouter (giới hạn ≤ 10B)

## Lựa chọn mặc định

`qwen/qwen3-8b` — 8.2B tham số. Phù hợp nhất cho bài này nhờ hỗ trợ đa ngôn ngữ, reasoning, structured JSON và agent workflow. Source hiện khai báo model này tại `src/config.py`.

## Các lựa chọn nên benchmark thêm

1. `mistralai/ministral-3-8b-2512`: 8B, context lớn và thiên về tool/agent use; nên kiểm tra chính xác slug/provider trước khi đổi.
2. `ibm-granite/granite-4.1-8b`: 8B, hướng enterprise extraction/tool calling; phù hợp nếu ưu tiên structured output.
3. Một model 4B như Gemma 3 4B: rẻ hơn nhưng cần benchmark kỹ tiếng Việt và độ ổn định JSON.

Không chọn model MoE chỉ vì số active parameters dưới 10B nếu tổng parameter count vượt 10B; điều kiện đề bài ghi parameter size, nên dùng dense model ≤10B là cách hiểu an toàn nhất.

Để đổi model sau khi research: sửa `OPENROUTER_MODEL` và `MODEL_PARAMETERS_BILLION` trong `src/config.py`, chạy lại `python -m src.main` để cập nhật `metadata.json`.
