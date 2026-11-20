#!/usr/bin/env python3
"""Build Aura's large boot-time App on an isolated temporary arena stack."""
from pathlib import Path

root = Path(__file__).resolve().parent / "build/aura-fd6818b-port/src"
main = root / "main.rs"
text = main.read_text()
anchor = "/// The boot logo's 1 KB buffer exists only during this call"
addition = '''#[cfg(feature = "blowfang-host")]
struct BootAppArgs {
    slot: *mut core::mem::MaybeUninit<app::App>,
    radio: core::mem::MaybeUninit<Radio>,
    chip_id: u16,
    keypad: core::mem::MaybeUninit<Keypad>,
    storage: core::mem::MaybeUninit<Storage>,
    flashlight: core::mem::MaybeUninit<Flashlight>,
    fm_radio: core::mem::MaybeUninit<FmRadio<'static>>,
    cfg: ChannelConfig,
    syst: *mut cortex_m::peripheral::SYST,
    result: *mut app::App,
}

#[cfg(feature = "blowfang-host")]
unsafe extern "C" fn construct_app_on_boot_stack(raw: *mut core::ffi::c_void) {
    let args = unsafe { &mut *raw.cast::<BootAppArgs>() };
    let slot = unsafe { &mut *args.slot };
    let storage = unsafe { args.storage.assume_init_read() };
    let app = place_app(
        slot,
        unsafe { args.radio.assume_init_read() },
        args.chip_id,
        unsafe { args.keypad.assume_init_read() },
        storage,
        unsafe { args.flashlight.assume_init_read() },
        unsafe { args.fm_radio.assume_init_read() },
        args.cfg,
        unsafe { &mut *args.syst },
    );
    args.result = app;
}

#[cfg(feature = "blowfang-host")]
struct UiDrawArgs<'d, DI: display_interface::WriteOnlyDataCommand> {
    display: *mut Display<'d, DI>,
    app: *mut app::App,
}

#[cfg(feature = "blowfang-host")]
unsafe extern "C" fn draw_ui_on_arena_callback<'d, DI: display_interface::WriteOnlyDataCommand + 'static>(
    raw: *mut core::ffi::c_void,
) {
    let args = unsafe { &mut *raw.cast::<UiDrawArgs<'d, DI>>() };
    ui::draw(unsafe { &mut *args.display }, unsafe { &mut *args.app });
}

fn draw_ui_on_arena<'d, DI: display_interface::WriteOnlyDataCommand + 'static>(
    display: &mut Display<'d, DI>, app: &mut app::App,
) {
    #[cfg(feature = "blowfang-host")]
    {
        let mut args = UiDrawArgs { display, app };
        unsafe extern "C" {
            fn km_call_on_stack(
                stack_top: *mut u8,
                f: unsafe extern "C" fn(*mut core::ffi::c_void),
                arg: *mut core::ffi::c_void,
            );
        }
        unsafe {
            km_call_on_stack(
                hal::arena::end() as *mut u8,
                draw_ui_on_arena_callback::<DI>,
                (&mut args as *mut UiDrawArgs<'d, DI>).cast(),
            );
        }
    }
    #[cfg(not(feature = "blowfang-host"))]
    ui::draw(display, app);
}

#[cfg(feature = "blowfang-host")]
struct PollKeysArgs {
    app: *mut app::App,
    syst: *mut cortex_m::peripheral::SYST,
}

#[cfg(feature = "blowfang-host")]
unsafe extern "C" fn poll_keys_on_arena_callback(raw: *mut core::ffi::c_void) {
    let args = unsafe { &mut *raw.cast::<PollKeysArgs>() };
    unsafe { &mut *args.app }.poll_keys(unsafe { &mut *args.syst });
}

fn poll_keys_on_arena(app: &mut app::App, syst: &mut cortex_m::peripheral::SYST) {
    #[cfg(feature = "blowfang-host")]
    {
        let mut args = PollKeysArgs { app, syst };
        unsafe extern "C" {
            fn km_call_on_stack(
                stack_top: *mut u8,
                f: unsafe extern "C" fn(*mut core::ffi::c_void),
                arg: *mut core::ffi::c_void,
            );
        }
        unsafe {
            km_call_on_stack(
                hal::arena::end() as *mut u8,
                poll_keys_on_arena_callback,
                (&mut args as *mut PollKeysArgs).cast(),
            );
        }
    }
    #[cfg(not(feature = "blowfang-host"))]
    app.poll_keys(syst);
}

'''
if "struct BootAppArgs" not in text:
    if text.count(anchor) != 1:
        raise SystemExit(f"expected one main.rs anchor, got {text.count(anchor)}")
    text = text.replace(anchor, addition + anchor, 1)
old = '''    let mut app: &'static mut app::App = place_app(
        unsafe { &mut *core::ptr::addr_of_mut!(APP_SLOT) },
        radio,
        chip_id,
        keypad,
        storage,
        flashlight,
        fm_radio,
        ChannelConfig {'''
if "let mut args = BootAppArgs {" not in text and old in text:
    text = text.replace(old, '''    #[cfg(feature = "blowfang-host")]
    let mut app: &'static mut app::App = {
        let mut args = BootAppArgs {
            slot: core::ptr::addr_of_mut!(APP_SLOT),
            radio: core::mem::MaybeUninit::new(radio),
            chip_id,
            keypad: core::mem::MaybeUninit::new(keypad),
            storage: core::mem::MaybeUninit::new(storage),
            flashlight: core::mem::MaybeUninit::new(flashlight),
            fm_radio: core::mem::MaybeUninit::new(fm_radio),
            cfg: ChannelConfig {''', 1)
    tail = '''        },
        &mut cp.SYST,
    );

    #[cfg(feature = "blowfang-host")]
    app::pager::prepare_host_stack();'''
    if text.count(tail) != 1:
        raise SystemExit(f"expected one place_app tail, got {text.count(tail)}")
    text = text.replace(tail, '''            },
            syst: &mut cp.SYST,
            result: core::ptr::null_mut(),
        };
        unsafe extern "C" {
            fn km_call_on_stack(
                stack_top: *mut u8,
                f: unsafe extern "C" fn(*mut core::ffi::c_void),
                arg: *mut core::ffi::c_void,
            );
        }
        unsafe {
            km_call_on_stack(
                hal::arena::end() as *mut u8,
                construct_app_on_boot_stack,
                (&mut args as *mut BootAppArgs).cast(),
            );
            // The constructor used the arena only as temporary stack; start the runtime clean.
            core::ptr::write_bytes(hal::arena::base(), 0, hal::arena::ARENA_BYTES);
        }
        unsafe { &mut *args.result }
    };

    #[cfg(not(feature = "blowfang-host"))]
    let mut app: &'static mut app::App = place_app(
        unsafe { &mut *core::ptr::addr_of_mut!(APP_SLOT) },
        radio,
        chip_id,
        keypad,
        storage,
        flashlight,
        fm_radio,
        ChannelConfig {
            freq_hz: DEFAULT_FREQ_HZ,
            tx_freq_hz: DEFAULT_FREQ_HZ,
            wide_band: true,
            power: TxPower::Low,
            subaudio_tx: SubAudio::None,
            subaudio_rx: SubAudio::None,
            modulation: Modulation::Fm,
        },
        &mut cp.SYST,
    );''', 1)
else:
    if "let mut args = BootAppArgs {" not in text:
        raise SystemExit("could not find App::new call site")
helpers_anchor = "/// The boot logo's 1 KB buffer exists only during this call"
helper_addition = addition[addition.index("#[cfg(feature = \"blowfang-host\")]\nstruct UiDrawArgs"):]
if "fn draw_ui_on_arena" not in text:
    text = text.replace(helpers_anchor, helper_addition + helpers_anchor, 1)
text = text.replace("app.poll_keys(&mut cp.SYST);", "poll_keys_on_arena(&mut app, &mut cp.SYST);")
text = text.replace("ui::draw(&mut display, &mut app);", "draw_ui_on_arena(&mut display, &mut app);")
main.write_text(text)
