#[cfg(k6_designer)]
#[inline(never)]
pub(super) fn draw_flat_widget<D:DrawTarget<Color=BinaryColor>>(
    lcd:&mut D,app:&app::App,w:&[u8;5],theme:&super::theme_reader::Theme,battery:&[u8;24],meter:&[u8;14],font:&[u8;132],modes:&[[&str;8];2],
){
    let tx=app.is_transmitting();
    let source=w[0];let x=i32::from(w[1]);let y=i32::from(w[2]);
    let Some((width,height))=super::theme_reader::widget_size(source,w[3]) else{return;};
    if w[4]&1!=0 && !matches!(source,9|14|15..=28) {lcd.fill_solid(&Rectangle::new(Point::new(x,y),Size::new(u32::from(width),u32::from(height))),BinaryColor::Off).ok();}
    let p=Point::new(x,y+FONT_5X8.baseline as i32);
    let mut line:TextBuf<20>=TextBuf::new();
    match source {
        0=>{
            if tx {
                let secs=app.tx_elapsed_seconds();
                write!(line,"{:02}:{:02}",secs/60,secs%60).ok();
                designer_small(lcd,line.as_str(),p);
                return;
            }
            let c=&app.settings().aprs_callsign;
            let len=c.iter().position(|&b|b==0).unwrap_or(6).min(6);
            if let Ok(s)=core::str::from_utf8(&c[..len]){designer_small(lcd,s,p);}
        }
        32|33=>{let side=usize::from(source-32);if app.side_is_channel_mode(side){designer_small(lcd,app.side_name_str(side),p);}}
        29=>{if !tx && app.power_save_active(){designer_small(lcd,"PS",p);}}
        30=>{if app.vox_enabled(){designer_small(lcd,"VX",p);}}
        31=>{if app.is_key_locked(){designer_small(lcd,"LK",p);}}
        1=>{if !tx && app.dual_standby_enabled(){designer_small(lcd,"DW",p);}}
        2=>{
            if theme.battery_offset!=0 {designer_bitmap(lcd,battery,17,Point::new(x,y));}
            else {super::icons::draw_battery(lcd,x+17,y,app.battery_bars()+2);}
        }
        3=>{if tx {designer_small(lcd,"MIC",p);}else if app.audio_open(){
            let over=app.s_meter_over_s9_dbm();
            if over>0 {write!(line,"{}dBm +{}",app.rssi_dbm(),over).ok();}
            else {write!(line,"{}dBm S{}",app.rssi_dbm(),app.s_meter_s_number()).ok();}
            designer_small(lcd,line.as_str(),p);
        }}
        4=>{if tx || app.audio_open(){
            // Reuse the theme's existing 13-segment geometry for microphone level.
            let n=if tx {((u16::from(app.mic_level())*13+127)/255) as u8} else {app.s_meter_level().min(13)};
            if theme.meter==0 {draw_classic_bar(lcd,x,y+7,n,13);}
            else if theme.meter==2 {
                for (count,dx,filled) in [(n.min(9),0,true),(n.saturating_sub(9),45,false)] {
                    if count==0{continue;}
                    let style=if filled{PrimitiveStyle::with_fill(BinaryColor::On)}else{PrimitiveStyle::with_stroke(BinaryColor::On,1)};
                    Rectangle::new(Point::new(x+dx,y+1),Size::new(u32::from(count)*5-1,7)).into_styled(style).draw(lcd).ok();
                }
            }else{
                for i in 0..n {
                    let p=Point::new(x+i32::from(i)*5,y+1);
                    if theme.meter==3 {designer_bitmap(lcd,if i<9{&meter[..7]}else{&meter[7..]},5,p);}
                    else {let style=if i<9{PrimitiveStyle::with_fill(BinaryColor::On)}else{PrimitiveStyle::with_stroke(BinaryColor::On,1)};Rectangle::new(p,Size::new(4,7)).into_styled(style).draw(lcd).ok();}
                }
            }
        }}
        9|14|15..=28=>{
            let (side,field)=match source {9=>(0,0),14=>(1,0),15..=21=>(0,usize::from(source-14)),_=>(1,usize::from(source-21))};
            let label=modes[side][field];
            // Empty conditional fields neither draw nor erase their neighbors.
            if !label.is_empty(){
                if w[4]&1!=0 {lcd.fill_solid(&Rectangle::new(Point::new(x,y),Size::new((label.len()*4) as u32,6)),BinaryColor::Off).ok();}
                Text::new(label,Point::new(x,y+FONT_4X6.baseline as i32),MonoTextStyle::new(&FONT_4X6,BinaryColor::On)).draw(lcd).ok();
            }
        }
        5..=14=>{
            let side=usize::from((source-5)/5);
            match (source-5)%5 {
                0=>{if app.master_index()==side{
                    if theme.marker.iter().any(|&b|b!=0){designer_bitmap(lcd,&theme.marker,7,Point::new(x,y));}
                    else{designer_small(lcd,">",p);}
                }}
                1=>{
                    let state=if tx {if app.master_index()==side {"TX"}else{""}}else if app.watching_index()==side && (app.is_monitor()||app.rssi_open()) && app.rx_blink_on(){"RX"}else if app.last_signal_side()==Some(side){">>"}else{""};
                    designer_small(lcd,state,p);
                }
                2=>{
                    if app.side_is_channel_mode(side){write!(line,"M{}",app.side_channel_num(side)).ok();}
                    else{write!(line,"VFO").ok();}
                    designer_small(lcd,line.as_str(),p);
                }
                3=>{
                    let freq_hz=if tx && app.master_index()==side {app.side_tx_freq_hz(side)}else{app.side_freq_hz(side)};
                    let channel_name=app.side_name_str(side);
                    let show_name=!theme.separate_names && app.side_is_channel_mode(side) && !channel_name.is_empty()
                        && app.channel_display_mode()!=app::ChannelDisplayMode::Frequency;
                    if show_name {
                        let shown=channel_name.get(..channel_name.len().min(12)).unwrap_or(channel_name);
                        designer_small(lcd,shown,p);
                        if app.channel_display_mode()==app::ChannelDisplayMode::NameFreq {
                            format_designer_frequency(&mut line,freq_hz);
                            designer_small(lcd,line.as_str(),Point::new(p.x,p.y+8));
                        }
                    } else if w[3]==0{draw_frequency_at(lcd,freq_hz,y+PROFONT_14_POINT.baseline as i32,x-44);}
                    else{
                        format_designer_frequency(&mut line,freq_hz);
                        if w[3]==2 {
                            for (i,ch) in line.as_str().bytes().enumerate(){
                                let glyph=match ch {b'0'..=b'9'=>usize::from(ch-b'0'),b'.'=>10,_=>continue};
                                let at=glyph*12;
                                designer_bitmap(lcd,&font[at..at+12],8,Point::new(x+8*i as i32,y));
                            }
                        }else{Text::new(line.as_str(),Point::new(x,y+FONT_6X10.baseline as i32),MonoTextStyle::new(&FONT_6X10,BinaryColor::On)).draw(lcd).ok();}
                    }
                }
                _=>{}
            }
        }
        _=>{}
    }
}

#[cfg(k6_designer)]
#[inline(never)]
fn format_designer_frequency(line:&mut TextBuf<20>,hz:u32){write!(line,"{:3}.{:05}",hz/1_000_000,(hz%1_000_000)/10).ok();}


#[cfg(k6_designer)]
#[inline(never)]
fn designer_bitmap<D:DrawTarget<Color=BinaryColor>>(lcd:&mut D,data:&[u8],width:u32,p:Point){
    Image::new(&embedded_graphics::image::ImageRaw::<BinaryColor>::new(data,width),p).draw(lcd).ok();
}
#[cfg(k6_designer)]
#[inline(never)]
fn designer_small<D:DrawTarget<Color=BinaryColor>>(lcd:&mut D,text:&str,p:Point){
    Text::new(text,p,MonoTextStyle::new(&FONT_5X8,BinaryColor::On)).draw(lcd).ok();
}
