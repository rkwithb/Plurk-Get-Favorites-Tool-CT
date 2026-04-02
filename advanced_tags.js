// advanced_tags.js
// Handles tag operations for Advanced Mode (Flask server required)

class TagManager {
    /**
     * Sends a POST request to add a single tag and updates the UI instantly.
     */
    static async addTag(inputElement, plurkId) {
        const newTag = inputElement.value.trim();
        if (!newTag) return;

        try {
            const response = await fetch('/api/tags', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ plurk_id: plurkId, tag_name: newTag })
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const result = await response.json();

            if (result.ok) {
                // Construct the HTML for the new tag pill
                const pillHtml = `
                    <span class="tag-pill">
                        ${newTag}
                        <a href="#" onclick="TagManager.removeTag(this.parentElement, ${plurkId}, '${newTag}'); return false;">✖</a>
                    </span>`;

                // Append the new tag pill to the container before the input field
                const editorDiv = inputElement.closest('.tag-editor');
                const tagsContainer = editorDiv.querySelector('.tags-container');
                tagsContainer.insertAdjacentHTML('beforeend', pillHtml);

                // Clear the input field for the next tag
                inputElement.value = '';
                console.log(`[DEBUG] Tag '${newTag}' saved to DB for plurk_id=${plurkId}`);
            } else {
                throw new Error(result.error || 'Server rejected the tag');
            }
        } catch (err) {
            console.error("Tag addition error:", err);
            alert("標籤新增失敗，請檢查 Server 狀態。");
        }
    }

    /**
     * Sends a DELETE request to remove a single tag and removes it from the UI.
     */
    static async removeTag(pillElement, plurkId, tagName) {
        try {
            const response = await fetch('/api/tags', {
                method: 'DELETE',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ plurk_id: plurkId, tag_name: tagName })
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const result = await response.json();

            if (result.ok) {
                // Remove the tag pill directly from the DOM
                pillElement.remove();
                console.log(`[DEBUG] Tag '${tagName}' removed from plurk_id=${plurkId}`);
            } else {
                throw new Error(result.error || 'Server rejected the deletion');
            }
        } catch (err) {
            console.error("Tag deletion error:", err);
            alert("標籤移除失敗，請檢查 Server 狀態。");
        }
    }
}

// Expose to window so inline onclick handlers in index.html can access it
window.TagManager = TagManager;
