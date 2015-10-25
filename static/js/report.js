(function() {

  function initWebSocket(baseUrl) {
    var signedRequest = $("meta[name=acpt]").attr("content");

    var uri = new URI(baseUrl);
    var socket = new WebSocket((uri.protocol() === "https" ? "wss://" : "ws://") +
            uri.hostname() +  (uri.port() ? (":" + uri.port()) : "") + "/websocket?signed_request=" + signedRequest);

    socket.onmessage = function(event) {
      var message = JSON.parse(event.data);
      var existingStatus = $("[data-user-id=" + message["user_id"] + "]");
      if (existingStatus.length > 0) {
        if (message.html !== "") {
          existingStatus.replaceWith(message.html);
        } else {
          existingStatus.remove();
        }
      } else {
        $(".statuses").append(message.html);
      }

    };
  }

  $(document).ready(function() {

    var baseUrl = $("meta[name=base-url]").attr("content");

    var $spinner = $(".spinner-container");
    $spinner.spin("medium");

    initWebSocket(baseUrl);

    $.ajax({
      url: baseUrl + "/status_view",
      type: "GET",
      dataType: "html",
      cache: false
    }).done(function(html) {
      $spinner.data().spinner.stop();
      $(".statuses").append(html);
    });

    $(".create-new-button").click(function(e) {

      AP.require('dialog', function(dialog) {
        dialog.open({
          key: "hcstandup.dialog"
        });
      });
    });
  });



})();