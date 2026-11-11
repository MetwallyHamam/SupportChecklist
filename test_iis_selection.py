from modules.iis_selection import (
    external_child_app_keys,
    is_windows_path_within,
    site_child_selection,
)


def test_windows_path_containment_is_case_insensitive_and_boundary_safe():
    assert is_windows_path_within(r"D:\Web\Site\Child", r"d:\web\site")
    assert is_windows_path_within(r"D:\Web\Site", r"D:\Web\Site")
    assert not is_windows_path_within(r"D:\Web\SiteTwo", r"D:\Web\Site")
    assert not is_windows_path_within(r"E:\Child", r"D:\Web\Site")


def test_external_children_are_selected_only_for_their_selected_site():
    apps = [
        {"site": "Portal", "path": "/Inside", "physical_path": r"D:\Web\Portal\Inside"},
        {"site": "Portal", "path": "/External", "physical_path": r"E:\Apps\External"},
        {"site": "Other", "path": "/External", "physical_path": r"E:\Apps\Other"},
    ]

    assert external_child_app_keys({"Portal": r"D:\Web\Portal"}, apps) == {
        "Portal|/External"
    }


def test_site_selection_backs_up_external_children_but_cleans_all_children():
    apps = [
        {"site": "Millensys", "path": "/Inside", "physical_path": r"C:\Millensys\Sites\Inside"},
        {"site": "Millensys", "path": "/External", "physical_path": r"D:\Apps\External"},
        {"site": "Other", "path": "/Ignored", "physical_path": r"E:\Apps\Ignored"},
    ]

    selection = site_child_selection(
        {"Millensys": r"C:\Millensys\Sites"}, apps
    )

    assert selection.visible_keys == {"Millensys|/Inside", "Millensys|/External"}
    assert selection.backup_keys == {"Millensys|/External"}
    assert selection.cleanup_keys == {"Millensys|/Inside", "Millensys|/External"}
