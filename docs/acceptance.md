# Приймання AT-01–AT-38

Оцінка локальної версії Windows, 20.10.2026. «Виконано» означає, що очікуваний
результат підтверджений наведеними автоматизованими перевірками; це не означає
ручний прогін усіх комбінацій. Перелік ручних сценаріїв — нижче. Інтеграції
з відкладеним модулем дорожніх листів позначені частковими.

| ID | Статус | Перевірка / практична межа |
| --- | --- | --- |
| AT-01 | виконано | [test_minimum_optional_numbers_and_no_reading](../tests/test_services.py), [test_ui_create_then_edit](../tests/test_ui.py) |
| AT-02 | виконано | [test_minimum_optional_numbers_and_no_reading](../tests/test_services.py); UUID незалежний від номера |
| AT-03 | виконано | [test_optional_reading_zero_journal_dates_and_source](../tests/test_accounting_registry.py); None відмінний від 0 |
| AT-04 | виконано | [test_trailer_needs_no_engine](../tests/test_services.py); «не застосовується» не є пропуском двигуна |
| AT-05 | виконано | [test_nonstandard_vin_with_explanation](../tests/test_services.py) |
| AT-06 | виконано | [test_duplicate_roles_reasons_and_audit](../tests/test_history.py), [test_physical_duplicate_operator_blocked_admin_approves](../tests/test_ui.py) |
| AT-07 | виконано | ті самі рольові/UI перевірки; погодження незмінні й аудовані |
| AT-08 | виконано | [test_military_duplicate_reason_and_confirmation_ui](../tests/test_ui.py), [test_import_duplicate_approvals_and_stale_preview](../tests/test_excel.py) |
| AT-09 | виконано | [test_historic_and_archive_identifiers_are_checked](../tests/test_history.py), [test_duplicates_include_archive_and_preserve_original](../tests/test_services.py) |
| AT-10 | виконано | [test_updates_keep_id_check_version_and_audit](../tests/test_services.py): техстан змінюється, категорія зберігається |
| AT-11 | виконано | [test_dated_kinds](../tests/test_history.py), [test_initial_unknown_dates_and_audit](../tests/test_history.py); документ не обов'язковий |
| AT-12 | виконано | [test_history_date_move_reconciles_both_neighbors](../tests/test_history.py), [test_history_edit_ui_moves_date_atomically](../tests/test_ui.py) |
| AT-13 | частково | [test_applied_document_blocks_and_rolls_back_all_fields](../tests/test_history.py), [test_document_conflict_ui_identifies_reference](../tests/test_ui.py), [test_norms_dates_preview_reconcile_and_lock](../tests/test_accounting_registry.py): захищений контракт; справжнього модуля листів немає |
| AT-14 | виконано | ручний браузерний сценарій M-01; [test_independent_draft_navigation_and_cancel_ui](../tests/test_ui.py), [test_editor_reload_preserves_legacy_draft](../tests/test_ui.py). Закриття браузера не захищене |
| AT-15 | виконано | [test_cancel_restores_last_saved_card](../tests/test_ui.py), скасування окремих форм у [test_independent_draft_navigation_and_cancel_ui](../tests/test_ui.py) |
| AT-16 | виконано | [test_bundle_late_failure_rolls_back_card_records_files_and_audit](../tests/test_card_bundle.py), [test_complete_card_one_transaction](../tests/test_card_bundle.py), [test_whole_card_confirmed_save_includes_note_and_equipment](../tests/test_ui.py) |
| AT-17 | виконано | [test_conflict_keeps_unsaved_draft](../tests/test_ui.py), [test_note_ui_uses_opened_version_and_preserves_conflict_draft](../tests/test_ui.py), [test_bundle_conflict_and_rights_preserve_other_changes](../tests/test_card_bundle.py) |
| AT-18 | виконано | [test_clone_whitelist_unique_id_and_no_independent_records](../tests/test_accounting_registry.py), [test_accounting_norm_and_clone_ui](../tests/test_ui.py) |
| AT-19 | виконано | [test_mvo_periods_and_personnel_rights](../tests/test_history.py), [test_dated_mvo_requires_preview_and_confirmation](../tests/test_ui.py) |
| AT-20 | виконано | [test_notes_equipment_and_document_download_ui](../tests/test_ui.py), [test_equipment_removed_is_retained_with_version](../tests/test_records.py) |
| AT-21 | виконано | [test_equipment_removed_is_retained_with_version](../tests/test_records.py); зняття не видаляє позицію |
| AT-22 | виконано | [test_multiple_files_versions_and_access](../tests/test_records.py), [test_backup_restore_db_and_all_file_versions](../tests/test_records.py). Ручний OS file picker не перевірений |
| AT-23 | виконано | [test_notes_versions_hidden_and_order](../tests/test_records.py); M-02 перевіряє збереження у браузері |
| AT-24 | виконано | [test_status_archive_and_filters](../tests/test_services.py), [test_registry_search_archive_and_columns](../tests/test_ui.py). Повнота — рекомендація щодо релевантних полів, не заборона запису |
| AT-25 | виконано | [test_screen_export_order_types_blanks_and_formula_safety](../tests/test_excel.py), [test_registry_explicit_columns_and_sort_ui](../tests/test_ui.py). Сортування/колонки задаються явними контролами |
| AT-26 | виконано | [test_full_export_selection_all_and_template](../tests/test_excel.py), [test_excel_page_and_explicit_full_export_ui](../tests/test_ui.py) |
| AT-27 | виконано | [test_actual_downloaded_template_import](../tests/test_excel.py), [test_excel_import_preview_confirm_and_mapping_change_ui](../tests/test_ui.py) |
| AT-28 | виконано | [test_arbitrary_mapping_manual_numeric_id_and_coordinates_ignored](../tests/test_excel.py), UI мапінгу в [test_excel_import_preview_confirm_and_mapping_change_ui](../tests/test_ui.py) |
| AT-29 | виконано | [test_preview_no_write_import_skips_errors_logs_and_repeat](../tests/test_excel.py); UI має журнал і XLSX-звіт |
| AT-30 | виконано | [test_identifier_free_import_is_idempotent](../tests/test_excel.py), [test_preview_no_write_import_skips_errors_logs_and_repeat](../tests/test_excel.py), [test_import_receipt_failure_rolls_back_vehicle](../tests/test_excel.py) |
| AT-31 | виконано | [test_viewer_blocked_in_services](../tests/test_services.py), [test_viewer_ui_read_only](../tests/test_ui.py), [test_reader_cannot_write_records](../tests/test_records.py), права імпорту/вкладень |
| AT-32 | виконано | [test_delete_rights_tombstone_and_dependencies](../tests/test_history.py); приховування кнопки не є єдиним захистом |
| AT-33 | виконано | та сама сервісна перевірка, [test_admin_deletes_erroneous_card_ui](../tests/test_ui.py), [test_independent_records_block_deletion](../tests/test_records.py) |
| AT-34 | частково | [test_delete_rights_tombstone_and_dependencies](../tests/test_history.py): застосований інтеграційний знімок блокує видалення; наскрізного реального листа немає |
| AT-35 | виконано | [test_status_archive_and_filters](../tests/test_services.py), [test_registry_search_archive_and_columns](../tests/test_ui.py) |
| AT-36 | виконано | [test_updates_keep_id_check_version_and_audit](../tests/test_services.py), [test_initial_unknown_dates_and_audit](../tests/test_history.py); загальний аудит також містить документи/норми/обладнання/примітки |
| AT-37 | виконано | [test_restore_replaces_existing_database_and_attachment_tree](../tests/test_handover.py), [test_restore_rolls_back_if_attachment_install_fails](../tests/test_handover.py), [test_backup_restore_db_and_all_file_versions](../tests/test_records.py). Збій живлення не тестований |
| AT-38 | виконано | [test_populated_copy_upgrade_preserves_unknown_dates](../tests/test_stage2_migration.py), [test_migration_preserves_populated_documents_and_files](../tests/test_handover.py), [test_migration_idempotent_and_schema_matches](../tests/test_database.py) |

## Ручні UI-сценарії на синтетичній БД

Ізольована БД `.local/manual-qa/auto.sqlite3`, localhost:8502; робоча БД не
використовувалася. Сценарії виконані через браузер, не лише AppTest.

- M-01: відкрити картку DEMO, набрати примітку, перейти до технічних даних,
  повернутися — текст збережений; вихід до реєстру показує три варіанти,
  «Залишитися» утримує текст.
- M-02: явне збереження синтетичної примітки — показано текст, автора, UTC і версію.
  Виявлене під час перевірки повторне заповнення форми виправлене через покоління
  ключів віджетів; додана автоматична регресія скидання форми.
- M-03: фінальний сервер перезапущений з оновленим кодом; введена нова примітка,
  загальна кнопка «Зберегти» показала операції, прапорець підтвердження й запис
  однією транзакцією. У журналі є новий текст/автор/версія, поле нової примітки
  порожнє. Локальне підтвердження: `.local/manual-qa/final-ui.png`.

Не позначені пройденими: усі 38 сценаріїв вручну, OS file picker/антивірус,
усі формати вкладень у реальних версіях Office, закриття вкладки/втрата живлення,
чисте встановлення на іншому Windows ПК, мережевий доступ, справжні дорожні листи,
реальний імпорт у робочу БД. Запуск/резервування CLI перевірені локально й тестами.
