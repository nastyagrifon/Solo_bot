from .pricing import register_pricing


(
    ask_device_step,
    save_device_step,
    open_device_overrides_menu,
    choose_device_override_option,
    clear_device_overrides,
    save_device_override_price,
) = register_pricing("dev")
