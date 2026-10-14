package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"strings"
	"syscall"
	"time"
	"unsafe"
)

const token = "koridor-remote-9f3c1a7e"

const (
	wsOverlapped = 0x00CF0000
	wsChild      = 0x40000000
	wsVisible    = 0x10000000
	wsBorder     = 0x00800000
	wsVScroll    = 0x00200000
	esAutoH      = 0x0080
	esPassword   = 0x0020
	esMulti      = 0x0004
	esAutoV      = 0x0040
	esReadOnly   = 0x0800
	wmDestroy    = 0x0002
	wmCommand    = 0x0111
	wmTimer      = 0x0113
	wmSetFont    = 0x0030
	idURL        = 101
	idRefresh    = 102
	idStart      = 103
	idStop       = 104
	idKey        = 105
	idSecret     = 106
	idSave       = 107
	idLog        = 108
	idStatus     = 109
)

var (
	user32   = syscall.NewLazyDLL("user32.dll")
	kernel32 = syscall.NewLazyDLL("kernel32.dll")
	gdi32    = syscall.NewLazyDLL("gdi32.dll")

	registerClass = user32.NewProc("RegisterClassExW")
	createWindow  = user32.NewProc("CreateWindowExW")
	defWindowProc = user32.NewProc("DefWindowProcW")
	getMessage    = user32.NewProc("GetMessageW")
	translateMsg  = user32.NewProc("TranslateMessage")
	dispatchMsg   = user32.NewProc("DispatchMessageW")
	postQuit      = user32.NewProc("PostQuitMessage")
	setWindowText = user32.NewProc("SetWindowTextW")
	getWindowText = user32.NewProc("GetWindowTextW")
	sendMessage   = user32.NewProc("SendMessageW")
	setTimer      = user32.NewProc("SetTimer")
	showWindow    = user32.NewProc("ShowWindow")
	updateWindow  = user32.NewProc("UpdateWindow")
	loadCursor    = user32.NewProc("LoadCursorW")
	messageBox    = user32.NewProc("MessageBoxW")

	getModule = kernel32.NewProc("GetModuleHandleW")
	createFont = gdi32.NewProc("CreateFontW")

	hwndURL, hwndKey, hwndSecret, hwndLog, hwndStatus, hwndMain syscall.Handle
	font syscall.Handle
)

type wndclass struct {
	size      uint32
	style     uint32
	wndProc   uintptr
	clsExtra  int32
	wndExtra  int32
	instance  syscall.Handle
	icon      syscall.Handle
	cursor    syscall.Handle
	brush     syscall.Handle
	menu      *uint16
	className *uint16
	iconSm    syscall.Handle
}

type point struct{ x, y int32 }
type msg struct {
	hwnd    syscall.Handle
	message uint32
	wParam  uintptr
	lParam  uintptr
	time    uint32
	pt      point
}

func utf16(s string) *uint16 {
	p, _ := syscall.UTF16PtrFromString(s)
	return p
}

func textOf(hwnd syscall.Handle) string {
	buf := make([]uint16, 512)
	getWindowText.Call(uintptr(hwnd), uintptr(unsafe.Pointer(&buf[0])), uintptr(len(buf)))
	return strings.TrimSpace(syscall.UTF16ToString(buf))
}

func setText(hwnd syscall.Handle, s string) {
	setWindowText.Call(uintptr(hwnd), uintptr(unsafe.Pointer(utf16(s))))
}

func child(class, title string, style uintptr, x, y, w, h int, id int, parent syscall.Handle) syscall.Handle {
	hwnd, _, _ := createWindow.Call(0, uintptr(unsafe.Pointer(utf16(class))), uintptr(unsafe.Pointer(utf16(title))),
		uintptr(wsChild|wsVisible|wsBorder)|style, uintptr(x), uintptr(y), uintptr(w), uintptr(h),
		uintptr(parent), uintptr(id), 0, 0)
	sendMessage.Call(hwnd, wmSetFont, uintptr(font), 1)
	return syscall.Handle(hwnd)
}

func wndProc(hwnd syscall.Handle, message uint32, wParam, lParam uintptr) uintptr {
	switch message {
	case 0x0001: // WM_CREATE
		hwndURL = child("EDIT", "http://127.0.0.1:8080", esAutoH, 16, 36, 430, 28, idURL, hwnd)
		child("BUTTON", "Обновить", 0, 456, 36, 110, 28, idRefresh, hwnd)
		hwndStatus = child("STATIC", "Нет связи", 0, 16, 74, 550, 22, idStatus, hwnd)
		child("BUTTON", "Старт", 0, 16, 108, 120, 32, idStart, hwnd)
		child("BUTTON", "Стоп", 0, 146, 108, 120, 32, idStop, hwnd)
		child("STATIC", "API Key", 0, 16, 154, 200, 18, 1, hwnd)
		hwndKey = child("EDIT", "", esAutoH, 16, 174, 260, 26, idKey, hwnd)
		child("STATIC", "Secret", 0, 290, 154, 200, 18, 2, hwnd)
		hwndSecret = child("EDIT", "", esAutoH|esPassword, 290, 174, 276, 26, idSecret, hwnd)
		child("BUTTON", "Сохранить ключ", 0, 16, 210, 160, 30, idSave, hwnd)
		hwndLog = child("EDIT", "", esMulti|esAutoV|esReadOnly|uintptr(wsVScroll), 16, 254, 550, 180, idLog, hwnd)
		setTimer.Call(uintptr(hwnd), 1, 4000, 0)
		go pull()
		return 0
	case wmCommand:
		switch int(wParam & 0xffff) {
		case idRefresh:
			go pull()
		case idStart:
			go postAction("start", "", "")
		case idStop:
			go postAction("stop", "", "")
		case idSave:
			go postAction("save", textOf(hwndKey), textOf(hwndSecret))
		}
		return 0
	case wmTimer:
		go pull()
		return 0
	case wmDestroy:
		postQuit.Call(0)
		return 0
	}
	ret, _, _ := defWindowProc.Call(uintptr(hwnd), uintptr(message), wParam, lParam)
	return ret
}

type view struct {
	Running  bool     `json:"running"`
	Mode     string   `json:"mode"`
	Symbol   string   `json:"symbol"`
	Equity   float64  `json:"equity"`
	Gate     string   `json:"gate"`
	KeySaved bool     `json:"keySaved"`
	Logs     []string `json:"logs"`
}

func baseURL() string {
	raw := textOf(hwndURL)
	if raw == "" {
		return "http://127.0.0.1:8080"
	}
	return strings.TrimRight(raw, "/")
}

func call(method, action, apiKey, secret string) (view, error) {
	var body []byte
	if method == http.MethodPost {
		payload := map[string]string{"action": action}
		if action == "save" {
			payload["apiKey"] = apiKey
			payload["secret"] = secret
		}
		body, _ = json.Marshal(payload)
	}
	req, err := http.NewRequest(method, baseURL()+"/api/remote", bytes.NewReader(body))
	if err != nil {
		return view{}, err
	}
	req.Header.Set("X-Koridor-Token", token)
	req.Header.Set("Content-Type", "application/json")
	client := http.Client{Timeout: 8 * time.Second}
	res, err := client.Do(req)
	if err != nil {
		return view{}, err
	}
	defer res.Body.Close()
	var out view
	if err := json.NewDecoder(res.Body).Decode(&out); err != nil {
		return view{}, err
	}
	return out, nil
}

func show(v view, err error) {
	if err != nil {
		setText(hwndStatus, "Нет связи: "+err.Error())
		return
	}
	state := "пауза"
	if v.Mode == "live" {
		state = "торгует"
	} else if v.Running {
		state = "ждёт"
	}
	key := "ключа нет"
	if v.KeySaved {
		key = "ключ записан"
	}
	pair := v.Symbol
	if pair == "" {
		pair = "без пары"
	}
	setText(hwndStatus, state+" · "+pair+" · "+trim(v.Gate)+" · "+key)
	setText(hwndLog, strings.Join(v.Logs, "\r\n"))
}

func trim(s string) string {
	if len(s) > 80 {
		return s[:80]
	}
	return s
}

func pull() {
	v, err := call(http.MethodGet, "", "", "")
	show(v, err)
}

func postAction(action, apiKey, secret string) {
	if action == "save" && (len(apiKey) < 8 || len(secret) < 8) {
		messageBox.Call(uintptr(hwndMain), uintptr(unsafe.Pointer(utf16("Вставьте API Key и Secret целиком."))), uintptr(unsafe.Pointer(utf16("Коридор"))), 0)
		return
	}
	v, err := call(http.MethodPost, action, apiKey, secret)
	show(v, err)
}

func main() {
	inst, _, _ := getModule.Call(0)
	cursor, _, _ := loadCursor.Call(0, 32512)
	class := utf16("KoridorRemote")
	wc := wndclass{
		size:      uint32(unsafe.Sizeof(wndclass{})),
		wndProc:   syscall.NewCallback(wndProc),
		instance:  syscall.Handle(inst),
		cursor:    syscall.Handle(cursor),
		brush:     6,
		className: class,
	}
	registerClass.Call(uintptr(unsafe.Pointer(&wc)))
	face, _, _ := createFont.Call(^uintptr(17), 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 0, 0, uintptr(unsafe.Pointer(utf16("Segoe UI"))))
	font = syscall.Handle(face)
	hwnd, _, _ := createWindow.Call(0, uintptr(unsafe.Pointer(class)), uintptr(unsafe.Pointer(utf16("Коридор — управление"))),
		wsOverlapped, 80, 80, 600, 490, 0, 0, inst, 0)
	hwndMain = syscall.Handle(hwnd)
	showWindow.Call(hwnd, 1)
	updateWindow.Call(hwnd)
	var m msg
	for {
		ret, _, _ := getMessage.Call(uintptr(unsafe.Pointer(&m)), 0, 0, 0)
		if int32(ret) <= 0 {
			break
		}
		translateMsg.Call(uintptr(unsafe.Pointer(&m)))
		dispatchMsg.Call(uintptr(unsafe.Pointer(&m)))
	}
}
