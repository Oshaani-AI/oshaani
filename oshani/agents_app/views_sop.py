"""SOP documentation views."""
from django.shortcuts import render
from django.http import HttpResponse
import logging

logger = logging.getLogger(__name__)

def system_sop(request):
    """System SOP documentation page with architecture and flow diagrams."""
    return render(request, 'system_sop.html')


def download_sop_pdf(request):
    """Generate and download SOP PDF from system_sop.html template."""
    try:
        # Try to use weasyprint for PDF generation
        try:
            from weasyprint import HTML
            from django.template.loader import render_to_string
            
            # Render the HTML template
            html_content = render_to_string('system_sop.html', {
                'request': request
            })
            
            # Add PDF-specific CSS to improve rendering
            pdf_css = """
            <style>
                @page {
                    size: A4;
                    margin: 2cm;
                }
                body {
                    font-family: 'DejaVu Sans', Arial, sans-serif;
                    line-height: 1.6;
                    color: #333;
                    background: white !important;
                }
                .sop-container {
                    background: white !important;
                    box-shadow: none !important;
                    padding: 20px !important;
                }
                .btn {
                    display: none !important;
                }
                a[href^="http"] {
                    color: #3498db;
                    text-decoration: underline;
                }
                pre {
                    page-break-inside: avoid;
                    overflow-wrap: break-word;
                }
                table {
                    page-break-inside: avoid;
                }
                .section {
                    page-break-inside: avoid;
                }
            </style>
            """
            
            # Insert PDF CSS before closing </head> tag
            if '</head>' in html_content:
                html_content = html_content.replace('</head>', pdf_css + '</head>')
            else:
                # If no head tag, prepend it
                html_content = pdf_css + html_content
            
            # Generate PDF from rendered HTML template
            # Use base_url to help resolve relative URLs and external resources
            try:
                pdf_content = HTML(string=html_content, base_url=request.build_absolute_uri('/')).write_pdf()
            except Exception as pdf_error:
                # If external CSS fails, try without base_url
                logger.warning(f"PDF generation with base_url failed: {pdf_error}, trying without base_url")
                pdf_content = HTML(string=html_content).write_pdf()
            
            # Return PDF as response
            response = HttpResponse(pdf_content, content_type='application/pdf')
            response['Content-Disposition'] = 'attachment; filename="Agent_Management_and_API_Usage_SOP.pdf"'
            return response
            
        except ImportError:
            # Fallback: return HTML page if weasyprint is not available
            logger.warning("weasyprint not installed, returning HTML page instead")
            return render(request, 'system_sop.html')
            
    except Exception as e:
        logger.error(f"Error generating SOP PDF: {str(e)}", exc_info=True)
        return HttpResponse(
            f"Error generating PDF: {str(e)}. Please ensure weasyprint is installed: pip install weasyprint",
            status=500
        )

