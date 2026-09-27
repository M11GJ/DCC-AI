"""Server-enforced conversation modes, independent of user-message templates."""
MODES = {'normal', 'plan', 'grill-me'}
POLICIES = {
 'plan': '現在は計画モードです。依頼された計画・提案の文章を、このモードのまま作成してください。目的、制約、具体的な実施手順、完了条件を日本語で示してください。軽微な不足情報は合理的な前提を明記し、計画を先に提示してください。禁止されるのは実際のファイル変更・送信・予約・コード実行などの外部操作です。計画書や説明文を書くことは禁止ではありません。計画作成のために通常モードへの切り替えを要求しないでください。質問用のask_user以外のツールは使えません。計画を提示して終了してください。実行可否の確認はシステムが純正の質問機能で表示します。自分で実行確認の質問を重複して出さないでください。',
 'grill-me': '現在は要件を掘り下げる質問モードです。曖昧さ、矛盾、優先順位、成功条件を確認する重要な質問を日本語で1〜3問ずつ行い、回答を待ってください。質問を一度に大量に並べず、実装や作業は行わないでください。十分に明確になったら合意事項と未解決事項をまとめてください。質問は可能な限りask_userで選択肢と自由入力を表示してください。質問以外のツールは使えません。',
 'cancelled': 'ユーザーは計画の実行を中止しました。実行せず、中止したことを日本語で簡潔に伝えて終了してください。追加質問やツール実行はしないでください。',
}
def enforce_mode(payload, mode):
 if mode not in POLICIES:return payload
 tools=[t for t in (payload.get('tools') or []) if mode in ('plan','grill-me') and (t.get('function') or {}).get('name')=='ask_user']
 return {**payload,'tools':tools, 'tool_choice':('auto' if tools else 'none'),'messages':[{'role':'system','content':POLICIES[mode]},*payload.get('messages',[])]}
