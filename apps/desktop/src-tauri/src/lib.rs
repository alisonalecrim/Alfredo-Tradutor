use serde_json::Value;

const ENGINE_BASE: &str = "http://127.0.0.1:8765";

#[tauri::command]
async fn engine_request(
    method: String,
    path: String,
    body: Option<Value>,
) -> Result<Value, String> {
    let url = format!(
        "{}{}",
        ENGINE_BASE,
        if path.starts_with('/') {
            path
        } else {
            format!("/{path}")
        }
    );

    let client = reqwest::Client::new();
    let method = method.to_uppercase();

    let request = match method.as_str() {
        "GET" => client.get(&url),
        "POST" => {
            let mut req = client.post(&url);
            if let Some(b) = body {
                req = req.json(&b);
            }
            req
        }
        other => return Err(format!("método HTTP não suportado: {other}")),
    };

    let response = request
        .send()
        .await
        .map_err(|e| format!("Falha ao falar com o motor ({url}): {e}"))?;

    let status = response.status();
    let text = response
        .text()
        .await
        .map_err(|e| format!("Resposta inválida do motor: {e}"))?;

    if !status.is_success() {
        return Err(format!("Motor {status}: {text}"));
    }

    if text.trim().is_empty() {
        return Ok(Value::Null);
    }

    serde_json::from_str(&text).map_err(|e| format!("JSON inválido do motor: {e} — {text}"))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![engine_request])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
