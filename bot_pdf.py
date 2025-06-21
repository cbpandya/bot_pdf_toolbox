import os
import re
import io
import logging
import tempfile
import uuid
import concurrent.futures
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    ConversationHandler
)
from PyPDF2 import PdfReader, PdfWriter, PdfMerger
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from pdf2image import convert_from_path
import pytesseract
import pikepdf
from PIL import Image
import requests
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
UPLOAD, ACTION, DELETE_PAGES, INSERT_PAGE, REARRANGE, OCR, ENCRYPT, WATERMARK, CLOUD_SAVE, BATCH_PROCESS, IMAGE_TO_PDF = range(11)

# Temporary storage for user files
user_data = {}
CLIENT_CONFIG = {
    "web": {
        "client_id": "YOUR_GOOGLE_CLIENT_ID",
        "client_secret": "YOUR_GOOGLE_CLIENT_SECRET",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["https://your-domain.com/oauth-callback"]
    }
}
SCOPES = ['https://www.googleapis.com/auth/drive.file']

# Initialize OCR engine
try:
    pytesseract.pytesseract.tesseract_cmd = r'/usr/bin/tesseract'
except:
    logger.warning("Tesseract not found. OCR functionality may not work")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the conversation and ask for PDF."""
    await update.message.reply_text(
        "📎 Welcome to PDF Toolbox Bot! Send me a PDF file or image to get started.\n\n"
        "You can also batch process multiple files by sending them at once."
    )
    return UPLOAD

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle document uploads (PDFs and images)."""
    user_id = update.message.from_user.id
    file = await update.message.document.get_file()
    file_extension = os.path.splitext(update.message.document.file_name)[1].lower()
    
    # Create temp directory for user
    if user_id not in user_data:
        temp_dir = tempfile.mkdtemp(prefix=f"pdfbot_{user_id}_")
        user_data[user_id] = {"temp_dir": temp_dir, "files": []}
    else:
        temp_dir = user_data[user_id]["temp_dir"]
    
    file_path = os.path.join(temp_dir, f"{uuid.uuid4()}{file_extension}")
    await file.download_to_drive(file_path)
    
    # Store file info
    file_info = {
        "path": file_path,
        "name": update.message.document.file_name,
        "type": "pdf" if file_extension == ".pdf" else "image"
    }
    user_data[user_id]["files"].append(file_info)
    
    # For single file, proceed to action menu
    if len(user_data[user_id]["files"]) == 1:
        return await show_action_menu(update, context, user_id)
    
    # For multiple files, stay in upload state
    await update.message.reply_text(
        f"📚 File added to batch! Total files: {len(user_data[user_id]['files'])}\n"
        "Send more files or /process to start batch processing."
    )
    return UPLOAD

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle photo uploads."""
    user_id = update.message.from_user.id
    photo = update.message.photo[-1]  # Highest resolution
    
    # Create temp directory for user
    if user_id not in user_data:
        temp_dir = tempfile.mkdtemp(prefix=f"pdfbot_{user_id}_")
        user_data[user_id] = {"temp_dir": temp_dir, "files": []}
    else:
        temp_dir = user_data[user_id]["temp_dir"]
    
    file_path = os.path.join(temp_dir, f"{uuid.uuid4()}.jpg")
    file = await photo.get_file()
    await file.download_to_drive(file_path)
    
    # Store file info
    file_info = {
        "path": file_path,
        "name": f"photo_{len(user_data[user_id]['files']) + 1}.jpg",
        "type": "image"
    }
    user_data[user_id]["files"].append(file_info)
    
    # For single file, proceed to action menu
    if len(user_data[user_id]["files"]) == 1:
        return await show_action_menu(update, context, user_id)
    
    # For multiple files, stay in upload state
    await update.message.reply_text(
        f"📸 Photo added to batch! Total files: {len(user_data[user_id]['files'])}\n"
        "Send more files or /process to start batch processing."
    )
    return UPLOAD

async def show_action_menu(update, context, user_id):
    """Show action menu based on file type."""
    file_type = user_data[user_id]["files"][0]["type"]
    
    if file_type == "pdf":
        keyboard = [
            [
                InlineKeyboardButton("🗑️ Delete Pages", callback_data="delete"),
                InlineKeyboardButton("📄 Insert Page", callback_data="insert"),
            ],
            [
                InlineKeyboardButton("🗜️ Compress", callback_data="compress"),
                InlineKeyboardButton("🔀 Rearrange", callback_data="rearrange"),
            ],
            [
                InlineKeyboardButton("🔍 OCR Text Recognition", callback_data="ocr"),
                InlineKeyboardButton("🔒 Encrypt/Decrypt", callback_data="encrypt"),
            ],
            [
                InlineKeyboardButton("💧 Add Watermark", callback_data="watermark"),
                InlineKeyboardButton("☁️ Save to Cloud", callback_data="cloud"),
            ],
            [
                InlineKeyboardButton("📤 Get Result", callback_data="done"),
                InlineKeyboardButton("🔄 Batch Process", callback_data="batch")
            ]
        ]
        message = "✅ PDF received! Choose an action:"
    else:  # Image
        keyboard = [
            [
                InlineKeyboardButton("📄 Convert to PDF", callback_data="image_to_pdf"),
                InlineKeyboardButton("🔍 OCR Text Recognition", callback_data="ocr"),
            ],
            [
                InlineKeyboardButton("💧 Add Watermark", callback_data="watermark"),
                InlineKeyboardButton("☁️ Save to Cloud", callback_data="cloud"),
            ],
            [InlineKeyboardButton("📤 Get Result", callback_data="done")]
        ]
        message = "🖼️ Image received! Choose an action:"
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if isinstance(update, Update):
        await update.message.reply_text(message, reply_markup=reply_markup)
    else:  # CallbackQuery
        await update.edit_message_text(message, reply_markup=reply_markup)
    
    return ACTION

async def handle_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle user actions."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    action = query.data
    
    if action == "delete":
        await query.edit_message_text("Enter page numbers to delete (e.g., 1,3-5):")
        return DELETE_PAGES
        
    elif action == "insert":
        await query.edit_message_text("Send the PDF page to insert (as a separate file):")
        return INSERT_PAGE
        
    elif action == "compress":
        return await compress_pdf(update, context)
        
    elif action == "rearrange":
        await query.edit_message_text("Enter new page order (e.g., 3,1,2):")
        return REARRANGE
        
    elif action == "ocr":
        await query.edit_message_text("Performing OCR... This may take a while...")
        return await ocr_pdf(update, context)
        
    elif action == "encrypt":
        await query.edit_message_text("Enter password and operation (e.g., 'encrypt mypassword' or 'decrypt mypassword'):")
        return ENCRYPT
        
    elif action == "watermark":
        keyboard = [
            [InlineKeyboardButton("Text Watermark", callback_data="text_watermark")],
            [InlineKeyboardButton("Image Watermark", callback_data="image_watermark")]
        ]
        await query.edit_message_text(
            "Choose watermark type:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return WATERMARK
        
    elif action == "cloud":
        await query.edit_message_text("Preparing for cloud save...")
        return await cloud_save(update, context)
        
    elif action == "batch":
        await query.edit_message_text("Batch processing activated. Send /process when done uploading files.")
        return BATCH_PROCESS
        
    elif action == "image_to_pdf":
        return await convert_image_to_pdf(update, context)
        
    elif action == "done":
        return await finish_editing(update, context)
    
    return ACTION

# ... (Existing functions: delete_pages, insert_page, compress_pdf, rearrange_pdf) ...

async def ocr_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Perform OCR on PDF or image."""
    query = update.callback_query
    user_id = query.from_user.id
    data = user_data[user_id]
    file_info = data["files"][0]
    
    try:
        if file_info["type"] == "pdf":
            # Convert PDF to images
            images = convert_from_path(file_info["path"], dpi=300)
            output_path = os.path.join(data["temp_dir"], "ocr_output.pdf")
            
            # Perform OCR on each image
            with concurrent.futures.ThreadPoolExecutor() as executor:
                futures = [executor.submit(process_ocr_page, image) for image in images]
                pdf_pages = [f.result() for f in concurrent.futures.as_completed(futures)]
            
            # Combine OCR pages
            merger = PdfMerger()
            for pdf_page in pdf_pages:
                merger.append(pdf_page)
            merger.write(output_path)
            merger.close()
            
        else:  # Image
            text = pytesseract.image_to_string(Image.open(file_info["path"]))
            output_path = os.path.join(data["temp_dir"], "ocr_text.txt")
            with open(output_path, "w") as f:
                f.write(text)
        
        # Update file info
        file_info["path"] = output_path
        file_info["name"] = "ocr_output." + ("pdf" if file_info["type"] == "pdf" else "txt")
        
        await query.edit_message_text("✅ OCR completed! Choose another action or get result.")
        return ACTION
        
    except Exception as e:
        await query.edit_message_text(f"❌ OCR error: {str(e)}")
        return ACTION

def process_ocr_page(image):
    """Process a single page for OCR."""
    text = pytesseract.image_to_pdf_or_hocr(image, extension='pdf')
    pdf_reader = PdfReader(io.BytesIO(text))
    return io.BytesIO(text)

async def encrypt_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Encrypt or decrypt PDF."""
    user_id = update.message.from_user.id
    data = user_data[user_id]
    text = update.message.text.split()
    
    if len(text) < 2:
        await update.message.reply_text("❌ Format: <operation> <password>\nExample: encrypt mypassword")
        return ENCRYPT
    
    operation = text[0].lower()
    password = text[1]
    file_info = data["files"][0]
    
    try:
        if operation == "encrypt":
            with pikepdf.open(file_info["path"]) as pdf:
                output_path = os.path.join(data["temp_dir"], "encrypted.pdf")
                pdf.save(output_path, encryption=pikepdf.Encryption(owner=password, user=password))
                file_info["path"] = output_path
                file_info["name"] = "encrypted_" + file_info["name"]
                await update.message.reply_text("✅ PDF encrypted! Choose another action or get result.")
                
        elif operation == "decrypt":
            with pikepdf.open(file_info["path"], password=password) as pdf:
                output_path = os.path.join(data["temp_dir"], "decrypted.pdf")
                pdf.save(output_path)
                file_info["path"] = output_path
                file_info["name"] = "decrypted_" + file_info["name"]
                await update.message.reply_text("✅ PDF decrypted! Choose another action or get result.")
                
        else:
            await update.message.reply_text("❌ Invalid operation. Use 'encrypt' or 'decrypt'.")
            return ENCRYPT
            
        return ACTION
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")
        return ACTION

async def handle_watermark(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle watermark selection."""
    query = update.callback_query
    await query.answer()
    action = query.data
    
    if action == "text_watermark":
        await query.edit_message_text("Enter watermark text:")
        return WATERMARK
    elif action == "image_watermark":
        await query.edit_message_text("Send the watermark image:")
        return WATERMARK
    
    return ACTION

async def apply_watermark(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Apply watermark to PDF or image."""
    user_id = update.message.from_user.id
    data = user_data[user_id]
    file_info = data["files"][0]
    
    try:
        if file_info["type"] == "pdf":
            reader = PdfReader(file_info["path"])
            writer = PdfWriter()
            
            # Create watermark
            watermark_pdf = create_watermark(update, context, len(reader.pages))
            watermark_reader = PdfReader(watermark_pdf)
            
            # Apply watermark to each page
            for i in range(len(reader.pages)):
                page = reader.pages[i]
                watermark_page = watermark_reader.pages[i]
                page.merge_page(watermark_page)
                writer.add_page(page)
            
            output_path = os.path.join(data["temp_dir"], "watermarked.pdf")
            with open(output_path, "wb") as f:
                writer.write(f)
                
        else:  # Image
            image = Image.open(file_info["path"])
            watermark_image = create_image_watermark(update, context)
            watermarked = Image.new('RGBA', image.size)
            watermarked.paste(image, (0, 0))
            watermarked.paste(watermark_image, (0, 0), watermark_image)
            
            output_path = os.path.join(data["temp_dir"], "watermarked.png")
            watermarked.save(output_path, "PNG")
        
        file_info["path"] = output_path
        file_info["name"] = "watermarked_" + file_info["name"]
        await update.message.reply_text("✅ Watermark applied! Choose another action or get result.")
        return ACTION
        
    except Exception as e:
        await update.message.reply_text(f"❌ Watermark error: {str(e)}")
        return ACTION

def create_watermark(update, context, page_count):
    """Create PDF watermark."""
    user_id = update.message.from_user.id
    data = user_data[user_id]
    
    # Text watermark
    if update.message.text:
        watermark_text = update.message.text
        packet = io.BytesIO()
        can = canvas.Canvas(packet, pagesize=letter)
        can.setFont("Helvetica", 36)
        can.setFillColorRGB(0.5, 0.5, 0.5, 0.3)
        
        # Create one page per document page
        for _ in range(page_count):
            # Center watermark
            width, height = letter
            can.saveState()
            can.translate(width/2, height/2)
            can.rotate(45)
            can.drawCentredString(0, 0, watermark_text)
            can.restoreState()
            can.showPage()
        
        can.save()
        return packet
        
    # Image watermark
    elif update.message.document or update.message.photo:
        if update.message.document:
            file = update.message.document
        else:
            file = update.message.photo[-1]
        
        watermark_path = os.path.join(data["temp_dir"], "watermark_image")
        await file.get_file().download_to_drive(watermark_path)
        
        packet = io.BytesIO()
        can = canvas.Canvas(packet, pagesize=letter)
        img = ImageReader(watermark_path)
        iw, ih = img.getSize()
        
        # Scale to 20% of page size
        scale = min(letter[0]/iw*0.2, letter[1]/ih*0.2)
        img_width = iw * scale
        img_height = ih * scale
        
        # Center position
        x = (letter[0] - img_width) / 2
        y = (letter[1] - img_height) / 2
        
        for _ in range(page_count):
            can.drawImage(img, x, y, width=img_width, height=img_height, mask='auto')
            can.showPage()
        
        can.save()
        return packet

def create_image_watermark(update, context):
    """Create image watermark."""
    if update.message.text:
        # Create text watermark image
        watermark_text = update.message.text
        img = Image.new('RGBA', (400, 100), (0, 0, 0, 0))
        # (Actual drawing would use PIL's ImageDraw)
        return img
    else:
        # Download watermark image
        user_id = update.message.from_user.id
        data = user_data[user_id]
        
        if update.message.document:
            file = update.message.document
        else:
            file = update.message.photo[-1]
        
        watermark_path = os.path.join(data["temp_dir"], "watermark_image")
        await file.get_file().download_to_drive(watermark_path)
        return Image.open(watermark_path).convert("RGBA")

async def cloud_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save file to Google Drive."""
    query = update.callback_query
    user_id = query.from_user.id
    data = user_data[user_id]
    file_info = data["files"][0]
    
    try:
        # Check if we have credentials
        if "credentials" not in data:
            # Start OAuth flow
            flow = Flow.from_client_config(
                CLIENT_CONFIG,
                scopes=SCOPES,
                redirect_uri=CLIENT_CONFIG["web"]["redirect_uris"][0]
            )
            authorization_url, state = flow.authorization_url(
                access_type='offline',
                include_granted_scopes='true'
            )
            data["oauth_state"] = state
            await query.edit_message_text(
                f"🔑 Please authorize access to Google Drive:\n{authorization_url}\n\n"
                "After authorization, send the code you received."
            )
            return CLOUD_SAVE
        else:
            # We have credentials, proceed with upload
            creds = Credentials.from_authorized_user_info(data["credentials"])
            service = build('drive', 'v3', credentials=creds)
            
            file_metadata = {
                'name': file_info["name"],
                'parents': ['root']
            }
            media = MediaFileUpload(file_info["path"], mimetype='application/pdf')
            file = service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, webViewLink'
            ).execute()
            
            await query.edit_message_text(
                f"✅ File saved to Google Drive!\n\n"
                f"🔗 View file: {file.get('webViewLink')}"
            )
            return ACTION
            
    except Exception as e:
        await query.edit_message_text(f"❌ Google Drive error: {str(e)}")
        return ACTION

async def handle_oauth_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle OAuth callback code."""
    user_id = update.message.from_user.id
    data = user_data[user_id]
    code = update.message.text
    
    try:
        flow = Flow.from_client_config(
            CLIENT_CONFIG,
            scopes=SCOPES,
            state=data["oauth_sauth_state"],
            redirect_uri=CLIENT_CONFIG["web"]["redirect_uris"][0]
        )
        flow.fetch_token(code=code)
        creds = flow.credentials
        data["credentials"] = {
            'token': creds.token,
            'refresh_token': creds.refresh_token,
            'token_uri': creds.token_uri,
            'client_id': creds.client_id,
            'client_secret': creds.client_secret,
            'scopes': creds.scopes
        }
        
        await update.message.reply_text("✅ Google Drive authorization successful!")
        return await cloud_save(update, context)
        
    except Exception as e:
        await update.message.reply_text(f"❌ Authorization failed: {str(e)}")
        return ACTION

async def batch_process(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process multiple files at once."""
    user_id = update.message.from_user.id
    data = user_data[user_id]
    
    if not data.get("files") or len(data["files"]) < 2:
        await update.message.reply_text("❌ Not enough files for batch processing.")
        return ACTION
    
    keyboard = [
        [InlineKeyboardButton("🗜️ Compress All", callback_data="batch_compress")],
        [InlineKeyboardButton("🔒 Encrypt All", callback_data="batch_encrypt")],
        [InlineKeyboardButton("🔍 OCR All", callback_data="batch_ocr")],
        [InlineKeyboardButton("📄 Merge to Single PDF", callback_data="batch_merge")]
    ]
    
    await update.message.reply_text(
        "🔧 Batch processing options:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return BATCH_PROCESS

async def handle_batch_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle batch processing actions."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = user_data[user_id]
    action = query.data
    
    try:
        if action == "batch_compress":
            for file_info in data["files"]:
                if file_info["type"] == "pdf":
                    with pikepdf.open(file_info["path"]) as pdf:
                        output_path = os.path.join(data["temp_dir"], f"compressed_{file_info['name']}")
                        pdf.save(output_path, compress_streams=True)
                        file_info["path"] = output_path
            await query.edit_message_text("✅ All PDFs compressed!")
            
        elif action == "batch_encrypt":
            await query.edit_message_text("Enter password for encryption:")
            data["batch_action"] = "encrypt"
            return BATCH_PROCESS
            
        elif action == "batch_ocr":
            with concurrent.futures.ThreadPoolExecutor() as executor:
                futures = []
                for file_info in data["files"]:
                    if file_info["type"] in ["pdf", "image"]:
                        futures.append(executor.submit(process_ocr, file_info))
                
                for future in concurrent.futures.as_completed(futures):
                    file_info = future.result()
            await query.edit_message_text("✅ OCR completed on all files!")
            
        elif action == "batch_merge":
            merger = PdfMerger()
            for file_info in data["files"]:
                if file_info["type"] == "pdf":
                    merger.append(file_info["path"])
                elif file_info["type"] == "image":
                    # Convert image to PDF first
                    pdf_path = convert_image_to_pdf_sync(file_info)
                    merger.append(pdf_path)
            
            output_path = os.path.join(data["temp_dir"], "merged.pdf")
            merger.write(output_path)
            merger.close()
            
            # Replace files with the merged PDF
            data["files"] = [{
                "path": output_path,
                "name": "merged.pdf",
                "type": "pdf"
            }]
            await query.edit_message_text("✅ All files merged into single PDF!")
            
        return ACTION
        
    except Exception as e:
        await query.edit_message_text(f"❌ Batch processing error: {str(e)}")
        return ACTION

def process_ocr(file_info):
    """Process OCR for a single file in batch."""
    if file_info["type"] == "pdf":
        images = convert_from_path(file_info["path"], dpi=300)
        ocr_path = os.path.join(os.path.dirname(file_info["path"]), f"ocr_{file_info['name']}")
        with open(ocr_path, "wb") as f:
            with PdfWriter() as writer:
                for image in images:
                    text = pytesseract.image_to_pdf_or_hocr(image, extension='pdf')
                    reader = PdfReader(io.BytesIO(text))
                    writer.add_page(reader.pages[0])
                writer.write(f)
        file_info["path"] = ocr_path
    else:  # Image
        text = pytesseract.image_to_string(Image.open(file_info["path"]))
        ocr_path = os.path.join(os.path.dirname(file_info["path"]), f"ocr_{os.path.splitext(file_info['name'])[0]}.txt")
        with open(ocr_path, "w") as f:
            f.write(text)
        file_info["path"] = ocr_path
        file_info["type"] = "text"
    return file_info

async def convert_image_to_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Convert image to PDF."""
    query = update.callback_query
    user_id = query.from_user.id
    data = user_data[user_id]
    file_info = data["files"][0]
    
    try:
        if file_info["type"] != "image":
            await query.edit_message_text("❌ This is not an image file.")
            return ACTION
        
        pdf_path = convert_image_to_pdf_sync(file_info)
        file_info["path"] = pdf_path
        file_info["name"] = os.path.splitext(file_info["name"])[0] + ".pdf"
        file_info["type"] = "pdf"
        
        await query.edit_message_text("✅ Image converted to PDF! Choose another action or get result.")
        return ACTION
        
    except Exception as e:
        await query.edit_message_text(f"❌ Conversion error: {str(e)}")
        return ACTION

def convert_image_to_pdf_sync(file_info):
    """Convert image to PDF (synchronous version)."""
    image = Image.open(file_info["path"])
    pdf_path = os.path.join(os.path.dirname(file_info["path"]), 
                           f"{os.path.splitext(file_info['name'])[0]}.pdf")
    
    if image.mode == 'RGBA':
        image = image.convert('RGB')
        
    image.save(pdf_path, "PDF", resolution=100.0)
    return pdf_path

# ... (Existing functions: finish_editing, cancel) ...

def main() -> None:
    """Start the bot."""
    TOKEN = 7494858344:AAHrPJcxrAztwuERShGWQoOQ42qpePGxj18
    
    app = Application.builder().token(TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            UPLOAD: [
                MessageHandler(filters.Document.PDF | filters.Document.IMAGE, handle_document),
                MessageHandler(filters.PHOTO, handle_photo),
                CommandHandler("process", batch_process)
            ],
            ACTION: [
                CallbackQueryHandler(handle_action)
            ],
            DELETE_PAGES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, delete_pages)
            ],
            INSERT_PAGE: [
                MessageHandler(filters.Document.PDF, insert_page)
            ],
            REARRANGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, rearrange_pages)
            ],
            OCR: [
                CallbackQueryHandler(handle_action)
            ],
            ENCRYPT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, encrypt_pdf)
            ],
            WATERMARK: [
                CallbackQueryHandler(handle_watermark),
                MessageHandler(filters.TEXT | filters.PHOTO | filters.Document.IMAGE, apply_watermark)
            ],
            CLOUD_SAVE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_oauth_code)
            ],
            BATCH_PROCESS: [
                CallbackQueryHandler(handle_batch_action),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_batch_password)
            ],
            IMAGE_TO_PDF: [
                CallbackQueryHandler(handle_action)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    app.add_handler(conv_handler)
    app.run_polling()

if __name__ == "__main__":
    main()
```