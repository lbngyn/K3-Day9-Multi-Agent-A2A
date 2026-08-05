# Báo cáo cá nhân — Multi-Agent A2A

> Đổi tên file theo 5 số cuối MSSV và họ tên trước khi nộp. Các trường nhận dạng bên dưới cần chính chủ điền.

## 1. Thông tin cá nhân

| Thông tin | Nội dung |
|---|---|
| Họ và tên | `Lê Bình Nguyên` |
| MSSV |`01659` |
| Khóa/Lớp | K3 |
| Vai trò chính | Thiết kế và triển khai multi-agent pipeline |
| Ngày hoàn thành | 2026-08-05 |

## 2. Phạm vi công việc

| Module/deliverable | File/hàm phụ trách | Input | Output | Trạng thái |
|---|---|---|---|---|
| Data access | `src/data_store.py` | CSV, case JSON | `CaseContext` | Hoàn thành |
| Specialist agents | `src/agents.py` | Context theo domain | Typed handoff | Hoàn thành |
| Orchestration/audit | `src/orchestrator.py` | Handoff | Output và trace | Hoàn thành |
| CLI/config | `src/main.py`, `src/config.py` | 50 input | 50 output, metadata | Hoàn thành |
| Verification | `VerifierAgent`, `tests/` | Draft output, CSV facts | Pass/fail | Hoàn thành |

## 3. Kết quả bàn giao và xác minh

- 50/50 case tạo được JSON đúng tên; phân bố: 8 seller-late, 8 logistics-late, 8 canceled-paid, 8 unavailable-paid, 9 split-payment, 9 unsupported claim.
- `trace.jsonl` có 300 event, tương ứng 6 event/ca: 3 specialist handoff, policy handoff, coordinator decision và verifier result.
- Evidence chỉ được dựng từ order/item/payment/seller thực tế và policy code hợp lệ.
- Hai test pipeline đều pass; output không vi phạm giới hạn 10 evidence.

Lệnh xác minh:

```bash
python -m unittest discover -s tests -v
python -m src.main
```

## 4. Giải thích kỹ thuật

Vấn đề cần giải quyết là phân biệt trách nhiệm seller, logistics và platform bằng dữ liệu kiểm chứng, thay vì tin hoàn toàn nội dung claim. `OlistStore` index dữ liệu theo `order_id`. Ba specialist xử lý độc lập order/seller, payment và delivery. Mỗi agent chỉ giao facts cùng evidence ID; Policy Agent áp dụng `EC_POLICY_V1` theo đúng thứ tự ưu tiên. Coordinator dựng output schema và Verifier đối chiếu evidence với các row tồn tại trước khi ghi file.

Contract chính là `CaseContext -> Handoff -> decision JSON`. Pipeline fail closed khi order không tồn tại, policy không hỗ trợ, case không khớp luật hoặc verifier phát hiện invariant sai. Tiền dùng `Decimal` và làm tròn hai chữ số; payment reconciliation dùng tolerance 0.10 BRL.

## 5. Quyết định kỹ thuật quan trọng

- Phương án cân nhắc: để LLM tự đọc toàn bộ CSV/prompt; hoặc dùng agent deterministic cho facts/policy và LLM chỉ diễn giải.
- Chọn phương án thứ hai vì kết quả chấm điểm cần tái lập, phép tính tiền phải chính xác và evidence giả bị phạt nặng.
- Provider là OpenRouter, model mặc định `qwen/qwen3-8b` (8.2B). Adapter nằm ở `src/openrouter.py`; model không có quyền ghi đè facts hoặc quyết định policy.
- Bằng chứng: 50 case chạy thành công, schema/evidence được verifier chấp nhận, unit tests pass.

## 6. Lỗi đã xử lý

- Triệu chứng: `AttributeError: 'int' object has no attribute 'quantize'` ở order không có item.
- Nguyên nhân: `sum()` trên collection rỗng trả integer `0`, trong khi hàm làm tròn yêu cầu `Decimal`.
- Cách sửa: truyền `Decimal("0")` làm giá trị khởi tạo cho mọi phép tổng tiền.
- Xác minh: chạy lại hai test và đủ 50 case thành công.
- Bài học: các aggregate tài chính phải giữ nguyên kiểu số cả khi tập dữ liệu rỗng.

## 7. Hiểu biết end-to-end

Case cung cấp `claimed_order_id`, dùng để join orders, items và payments. Specialist agents biến row CSV thành facts tối thiểu có nguồn gốc. Policy Agent xác định issue/root cause/responsibility/refund/action. Coordinator ghép kết quả, Verifier kiểm tra lại schema, enum, giới hạn, tiền và evidence. Output hợp lệ được ghi vào `output/`; mọi handoff được audit trong `trace.jsonl`, còn cấu hình môi trường chạy nằm trong `metadata.json`.

## 8. Cam kết

- [x] Báo cáo phản ánh phần triển khai và kết quả đã xác minh.
- [x] Có thể giải thích luồng end-to-end và contract giữa các agent.
- [x] Không chứa API key/token/secret.
- [ ] Chủ sở hữu đã điền và xác nhận thông tin cá nhân.

**Họ và tên:** `[CẦN ĐIỀN]`  
**Ngày xác nhận:** `[CẦN ĐIỀN]`
